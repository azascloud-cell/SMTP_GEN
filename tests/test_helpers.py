from bot.number_files import detect_region, _sanitize
from bot.number_manager import parse_numbers_from_text, pick_random, get_country_info

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
