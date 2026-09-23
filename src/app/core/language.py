from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal


LanguageCode = Literal["en", "vi"]
_LANGUAGE_PREFIX = re.compile(
    r"^\s*(?P<language>en|vi)(?:[-_][a-z]{2})?"
    r"(?:\s*(?:[:|,-])\s*|\s+)(?P<message>.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LanguageProfile:
    code: LanguageCode
    name: str
    autonomous_title: str
    syncing_status: str
    model_error: str
    conversation_not_found: str
    system_instruction: str

    def synced_files_status(self, count: int) -> str:
        if self.code == "vi":
            return f"Đã đồng bộ {count} tệp"
        return f"Synced {count} files"

    def attached_files_label(self, count: int) -> str:
        if self.code == "vi":
            return f"Đã đính kèm {count} tệp"
        return f"Attached {count} file(s)"

    def format_report_date(self, value: datetime) -> str:
        if self.code == "vi":
            return f"ngày {value.day} tháng {value.month} năm {value.year}"
        return value.strftime("%B %-d, %Y")


PROFILES: dict[LanguageCode, LanguageProfile] = {
    "en": LanguageProfile(
        code="en",
        name="English",
        autonomous_title="Autonomous Data Exploration",
        syncing_status="Syncing data...",
        model_error=(
            "This model is temporarily unavailable. Please switch to another model "
            "and try again."
        ),
        conversation_not_found="Conversation not found",
        system_instruction=(
            "The user's UI language preference is English. Reply in English by default "
            "and write reports in English by default."
        ),
    ),
    "vi": LanguageProfile(
        code="vi",
        name="Vietnamese",
        autonomous_title="Khám phá dữ liệu tự động",
        syncing_status="Đang đồng bộ dữ liệu...",
        model_error=(
            "Mô hình hiện tạm thời không khả dụng. Vui lòng chọn mô hình khác và thử lại."
        ),
        conversation_not_found="Không tìm thấy cuộc hội thoại",
        system_instruction=(
            "The user's UI language preference is Vietnamese. Reply in Vietnamese by default, "
            "write reports in Vietnamese by default, and use Vietnamese for chart titles, "
            "axis labels, legends, annotations, captions, and narrative text whenever possible. "
            "Use xelatex with fontspec for Vietnamese or mixed Vietnamese/English LaTeX/PDF "
            "outputs so Vietnamese diacritics render correctly. Localize report headings, "
            "title pages, summaries, dates, captions, and table labels. Keep technical names, "
            "code, column names, and file names unchanged when translating would reduce clarity."
        ),
    ),
}


def normalize_ui_language(value: str | None) -> LanguageCode:
    primary = (value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    if primary == "vi":
        return primary
    return "en"


def get_language_profile(value: str | None) -> LanguageProfile:
    return PROFILES[normalize_ui_language(value)]


def resolve_language_request(
    language: str | None,
    message: str,
) -> tuple[LanguageProfile, str]:
    """Resolve an explicit query prefix before falling back to the API locale."""
    value = (message or "").strip()
    match = _LANGUAGE_PREFIX.match(value)
    if match:
        return get_language_profile(match.group("language")), match.group(
            "message"
        ).strip()
    return get_language_profile(language), value
