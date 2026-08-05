from bot.number_files import detect_region, _sanitize
from bot.number_manager import parse_numbers_from_text, pick_random, get_country_info
import bot.whatsapp_fix
from pathlib import Path

def test_detect_region():
    assert detect_region("list_indonesia.txt") == "🇮🇩 Indonesia"
    assert detect_region("malaysia_leads.txt") == "🇲🇾 Malaysia"
    assert detect_region("random_numbers.txt") == ""

def test_sanitize():
    assert _sanitize("leads??!!.txt") == "leads____.txt"
    assert _sanitize("list") == "list.txt"

def test_parse_numbers():
    text = "081234567890\n+62811223344\n# comment\ninvalid_phone\n"
    numbers = parse_numbers_from_text(text)
    assert "+6281234567890" in numbers or "081234567890" in numbers or "+62811223344" in numbers

def test_pick_random():
    lst = ["1", "2", "3", "4", "5"]
    picked = pick_random(lst, 2)
    assert len(picked) == 2
    for item in picked:
        assert item in lst

def test_get_country_info():
    info = get_country_info("+6281234567890")
    assert info["code"] == "62"
    assert info["name"] == "Indonesia"

def test_get_recommended_banding_language():
    from bot.number_manager import get_recommended_banding_language
    assert get_recommended_banding_language("+628123456789") == "🇮🇩 Indonesia (ID)"
    assert get_recommended_banding_language("+22899999999") == "🇫🇷 Prancis (Français)"
    assert get_recommended_banding_language("+96655555555") == "🇸🇦 Arab (العربية)"
    assert get_recommended_banding_language("+79999999999") == "🇷🇺 Rusia (Русский)"
    assert get_recommended_banding_language("+12025550123") == "🇬🇧 Inggris (English)"

def test_format_banding_templates():
    from bot.number_manager import format_banding_templates
    templates = format_banding_templates("+22899999999")
    assert "TEMPLATE TEXT BANDING" in templates
    assert "🇫🇷 Prancis (Français)" in templates
    assert "+22899999999" in templates

def test_get_gmail_alias_and_increment(tmp_path):
    # Mock DAILY_USAGE_FILE to a temp directory
    temp_file = tmp_path / "test_smtp_daily_usage.json"
    original_file = bot.whatsapp_fix.DAILY_USAGE_FILE
    bot.whatsapp_fix.DAILY_USAGE_FILE = temp_file

    try:
        from bot.whatsapp_fix import get_gmail_alias_and_increment

        # Test non-gmail email
        alias, count = get_gmail_alias_and_increment("test@yahoo.com")
        assert alias == "test@yahoo.com"
        assert count == 0

        # Test gmail email - First send
        alias1, count1 = get_gmail_alias_and_increment("my.user@gmail.com")
        assert alias1 == "my.user+1@gmail.com"
        assert count1 == 1

        # Test gmail email - Second send
        alias2, count2 = get_gmail_alias_and_increment("my.user@gmail.com")
        assert alias2 == "my.user+2@gmail.com"
        assert count2 == 2

        # Test email with existing plus sign
        alias3, count3 = get_gmail_alias_and_increment("my.user+existing@gmail.com")
        assert alias3 == "my.user+3@gmail.com"
        assert count3 == 3

    finally:
        # Restore the original DAILY_USAGE_FILE
        bot.whatsapp_fix.DAILY_USAGE_FILE = original_file


def test_status_emoji_and_label():
    from bot.number_manager import status_emoji, status_label
    assert status_emoji(True) == "🔴"
    assert status_emoji(False) == "🟢"
    assert status_emoji(None) == "⚪"

    assert status_label(True) == "Terdaftar (Linked)"
    assert status_label(False) == "Fresh"
    assert status_label(None) == "Terdaftar (Unlinked)"


def test_get_masked_and_prefix():
    from bot.number_manager import get_masked_and_prefix
    masked, prefix = get_masked_and_prefix("+22879017409")
    assert masked == "228***7409"
    assert prefix == "228790"
