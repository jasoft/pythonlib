"""OCR 请求瞬态失败重试的回归测试。

背景（2026-10-24 真机事故）：OCR 服务由 AutoStopProxy 按需拉起，代理在上游瞬时
不可用时返回 502。``_predict_with_timing`` 原先把一次 HTTP 失败吞成 ``result=None``，
调用方因此看到「画面上没有这段文字」，而不是「这次没读到」——当天 06:27:54 的一次
502 就让「开启宝箱」误判宝库里没有「亡灵宝箱」，跳过了 1800 秒挂机补充奖励。

本测试锁三条不变量：

1. 瞬态错误（502 / 连接被重置）必须重试，并在成功时返回识别结果；
2. 不可重试的错误（4xx、超时）必须**一次就放弃** —— 重试超时会吃掉调用方看门狗预算；
3. 重试用尽后仍返回 ``None``，真故障不被掩盖成成功。
"""

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from vibe_ocr import ocr_helper
from vibe_ocr.ocr_helper import OCRHelper

#: 期望的重试上限（与生产常量对齐）。用 getattr 取值而不是直接 import，
#: 这样撤回生产改动后测试能正常跑起来、失败落在**行为断言**上，
#: 而不是以 ``ImportError`` 这种机械失败收场（那证明不了行为缺失）。
EXPECTED_MAX_ATTEMPTS = 3

_OK_PAYLOAD = {
    "errorCode": 0,
    "result": {
        "ocrResults": [
            {
                "prunedResult": {
                    "rec_texts": ["亡灵宝箱"],
                    "rec_scores": [0.99],
                    "dt_polys": [[[10, 10], [60, 10], [60, 30], [10, 30]]],
                }
            }
        ]
    },
}


class _FakeResponse:
    """最小化的 requests.Response 替身，只实现被测代码用到的两个方法。"""

    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Server Error", response=self
            )

    def json(self):
        return self._payload


@pytest.fixture()
def helper(tmp_path, monkeypatch):
    """构造一个不落网络、不睡真实时间的 OCRHelper。"""
    # 重试间隔压到 0，避免测试真的等待（旧代码没有该常量，raising=False 保证能跑）
    monkeypatch.setattr(
        ocr_helper, "OCR_RETRY_DELAYS_SECONDS", (0.0, 0.0), raising=False
    )
    image_path = tmp_path / "screen.png"
    # 内容不重要：resize_image=False 时不会读像素，只要文件存在
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return OCRHelper(
        output_dir=str(tmp_path / "out"),
        resize_image=False,
        snapshot_func=lambda *args, **kwargs: None,
    ), str(image_path)


def _patch_post(monkeypatch, outcomes):
    """把 requests.post 换成按序返回/抛出的假实现，并记录调用次数。"""
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ocr_helper.requests, "post", fake_post)
    return calls


def test_retries_transient_502_then_succeeds(helper, monkeypatch):
    """一次 502 后必须重试并拿到结果（改前只有 1 次调用、返回 None）。"""
    ocr, image_path = helper
    calls = _patch_post(
        monkeypatch, [_FakeResponse(502), _FakeResponse(200, _OK_PAYLOAD)]
    )

    result = ocr._predict_with_timing(image_path)

    assert len(calls) == 2, "瞬态 502 必须触发一次重试"
    assert result is not None, "重试成功后必须返回识别结果"
    assert result["rec_texts"] == ["亡灵宝箱"]


def test_retries_connection_reset(helper, monkeypatch):
    """连接被重置同样属于瞬态传输错误，必须重试。"""
    ocr, image_path = helper
    calls = _patch_post(
        monkeypatch,
        [
            requests.exceptions.ConnectionError("Connection reset by peer"),
            _FakeResponse(200, _OK_PAYLOAD),
        ],
    )

    result = ocr._predict_with_timing(image_path)

    assert len(calls) == 2
    assert result is not None


def test_gives_up_after_max_attempts(helper, monkeypatch):
    """持续 502 时按上限重试，最终仍返回 None（真故障不许伪装成功）。"""
    ocr, image_path = helper
    calls = _patch_post(monkeypatch, [_FakeResponse(502)])

    result = ocr._predict_with_timing(image_path)

    assert getattr(ocr_helper, "OCR_MAX_ATTEMPTS", 0) == EXPECTED_MAX_ATTEMPTS
    assert len(calls) == EXPECTED_MAX_ATTEMPTS
    assert result is None


def test_does_not_retry_client_error(helper, monkeypatch):
    """400/404 这类确定性错误重试没有意义，必须一次就放弃。"""
    ocr, image_path = helper
    calls = _patch_post(monkeypatch, [_FakeResponse(400)])

    result = ocr._predict_with_timing(image_path)

    assert len(calls) == 1
    assert result is None


def test_does_not_retry_read_timeout(helper, monkeypatch):
    """读超时不重试：60 秒一次已占掉看门狗预算的三分之一。"""
    ocr, image_path = helper
    calls = _patch_post(monkeypatch, [requests.exceptions.ReadTimeout("read timeout")])

    result = ocr._predict_with_timing(image_path)

    assert len(calls) == 1, "超时不得重试，否则会撑爆看门狗阈值"
    assert result is None


def test_does_not_retry_connect_timeout(helper, monkeypatch):
    """ConnectTimeout 同时是 ConnectionError，但语义属超时，必须排除在重试之外。"""
    ocr, image_path = helper
    calls = _patch_post(
        monkeypatch, [requests.exceptions.ConnectTimeout("connect timeout")]
    )

    result = ocr._predict_with_timing(image_path)

    assert len(calls) == 1
    assert result is None
