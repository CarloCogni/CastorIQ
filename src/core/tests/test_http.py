# core/tests/test_http.py
"""Tests for the toast helpers in core.http."""

import json

from django.http import HttpResponse, JsonResponse

from core.http import toast_response, trigger_toast


def test_trigger_toast_sets_header_with_default_success():
    response = HttpResponse(status=200)
    trigger_toast(response, "Saved")
    payload = json.loads(response["HX-Trigger"])
    assert payload == {"castor:toast": {"level": "success", "message": "Saved"}}


def test_trigger_toast_accepts_error_level():
    response = HttpResponse(status=400)
    trigger_toast(response, "Nope", level="error")
    payload = json.loads(response["HX-Trigger"])
    assert payload["castor:toast"] == {"level": "error", "message": "Nope"}


def test_trigger_toast_merges_with_existing_hx_trigger():
    response = HttpResponse(status=200)
    response["HX-Trigger"] = json.dumps({"other:event": {"foo": "bar"}})
    trigger_toast(response, "Saved")
    payload = json.loads(response["HX-Trigger"])
    assert payload["other:event"] == {"foo": "bar"}
    assert payload["castor:toast"] == {"level": "success", "message": "Saved"}


def test_trigger_toast_returns_same_response_for_chaining():
    response = HttpResponse(status=200)
    returned = trigger_toast(response, "Saved")
    assert returned is response


def test_trigger_toast_works_on_json_response():
    response = JsonResponse({"ok": True})
    trigger_toast(response, "Done")
    payload = json.loads(response["HX-Trigger"])
    assert payload["castor:toast"]["message"] == "Done"


def test_toast_response_default_is_empty_200_with_header():
    response = toast_response("Hello")
    assert response.status_code == 200
    assert response.content == b""
    payload = json.loads(response["HX-Trigger"])
    assert payload == {"castor:toast": {"level": "success", "message": "Hello"}}


def test_toast_response_respects_custom_status_and_level():
    response = toast_response("Bad input", level="error", status=400)
    assert response.status_code == 400
    payload = json.loads(response["HX-Trigger"])
    assert payload["castor:toast"] == {"level": "error", "message": "Bad input"}


def test_trigger_toast_escapes_quotes_in_message():
    response = HttpResponse(status=200)
    trigger_toast(response, 'He said "hi"')
    payload = json.loads(response["HX-Trigger"])
    assert payload["castor:toast"]["message"] == 'He said "hi"'
