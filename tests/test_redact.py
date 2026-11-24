from __future__ import annotations

from sms_wiz.redact import redact


def test_otp_masked():
    result = redact("482910 is your OTP", otp="482910")
    assert "482910" not in result.text
    assert result.text == "<otp> is your OTP"
    assert result.replaced["otp"] == 1


def test_short_numeric_code_is_not_partially_replaced():
    # "4829100" contains "482910" but must not be half-masked.
    result = redact("ref 4829100 ok", otp="482910")
    assert "4829100" in result.text


def test_voucher_code_and_pin_masked():
    result = redact("Voucher code: AMZP-4K9Q-7T2X. PIN: 982104.", codes=("AMZP-4K9Q-7T2X", "982104"))
    assert "AMZP-4K9Q-7T2X" not in result.text
    assert "982104" not in result.text
    assert result.replaced["code"] == 2


def test_pan_masked():
    result = redact("PAN ABCDE1234F submitted")
    assert "ABCDE1234F" not in result.text
    assert result.replaced["pan"] == 1


def test_account_number_masked_but_amounts_kept():
    result = redact("A/c 50100234567890 debited Rs 1,450.00")
    assert "50100234567890" not in result.text
    assert "1,450.00" in result.text


def test_phone_masked():
    result = redact("call 9876543210 now")
    assert "9876543210" not in result.text


def test_vpa_local_part_masked_domain_kept():
    result = redact("paid to KIRANAKART@axisbank", codes=())
    assert result.text == "paid to <vpa>@axisbank"


def test_url_masked():
    result = redact("click http://sbi-kyc.xyz/login")
    assert "sbi-kyc.xyz" not in result.text
    assert "<url>" in result.text


def test_clean_text_untouched():
    text = "Rs 249.00 spent at SWIGGY on 25-02-25"
    assert redact(text).text == text
