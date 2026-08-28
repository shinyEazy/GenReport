from datetime import datetime

from app.core.language import (
    get_language_profile,
    normalize_ui_language,
    resolve_language_request,
)


def test_normalize_ui_language_supports_vietnamese_locales() -> None:
    assert normalize_ui_language("vi") == "vi"
    assert normalize_ui_language("vi-VN") == "vi"
    assert normalize_ui_language("VI_vn") == "vi"


def test_normalize_ui_language_preserves_existing_fallbacks() -> None:
    assert normalize_ui_language("zh-CN") == "zh"
    assert normalize_ui_language("en-US") == "en"
    assert normalize_ui_language("fr-FR") == "en"
    assert normalize_ui_language(None) == "en"


def test_vietnamese_profile_localizes_report_generation() -> None:
    profile = get_language_profile("vi-VN")

    assert profile.name == "Vietnamese"
    assert profile.autonomous_title == "Khám phá dữ liệu tự động"
    assert profile.syncing_status == "Đang đồng bộ dữ liệu..."
    assert profile.synced_files_status(2) == "Đã đồng bộ 2 tệp"
    assert profile.conversation_not_found == "Không tìm thấy cuộc hội thoại"
    assert profile.attached_files_label(2) == "Đã đính kèm 2 tệp"
    assert profile.format_report_date(datetime(2026, 8, 14)) == "ngày 14 tháng 8 năm 2026"
    assert "Reply in Vietnamese by default" in profile.system_instruction
    assert "write reports in Vietnamese by default" in profile.system_instruction
    assert "xelatex" in profile.system_instruction


def test_message_prefix_overrides_default_language_and_is_removed() -> None:
    profile, message = resolve_language_request(
        "en",
        "vi Phân tích bộ dữ liệu này",
    )

    assert profile.code == "vi"
    assert message == "Phân tích bộ dữ liệu này"
