from app.services import normalize as N

GYM = "Men's Dry-Fit Athletic Gym Shorts w/ Pockets Pack of 2 (Black/Navy, L) - Running Workout"


def test_pack_count_variants():
    assert N.parse_pack_count(GYM) == 2
    assert N.parse_pack_count("Mesh 2-Pack Gray") == 2
    assert N.parse_pack_count("Set of 3 towels") == 3
    assert N.parse_pack_count("Single shirt") == 1


def test_sizes_and_supplier_tags():
    assert N.normalize_sizes(["Lrg"]) == ["L"]
    assert N.normalize_sizes(["S/M/L", "X-Large"]) == ["S", "M", "L", "XL"]
    assert N.normalize_sizes(["US 10"]) == ["10"]
    assert N.normalize_sizes(["7.5"]) == ["7.5"]
    assert N.normalize_sizes(["nonsense"]) == []


def test_colors_supplier_codes():
    assert N.normalize_colors(["Blk/Nvy"]) == ["Black", "Navy"]
    assert N.normalize_colors(["Black/Grey/Navy"]) == ["Black", "Gray", "Navy"]
    assert N.normalize_colors(["Gry/Rd", "Red"]) == ["Gray", "Red"]


def test_strip_title_noise():
    s = N.strip_title_noise(GYM)
    assert "Running Workout" not in s and "(" not in s and "Pack" not in s
    assert "with Pockets" in s


def test_price_parsing():
    assert N.parse_price("$24.99") == 24.99
    assert N.parse_price("USD 29.50") == 29.5
    assert N.parse_price("19,99") == 19.99
    assert N.parse_price("") is None
    assert N.parse_price("free") is None


def test_build_title_template():
    t = N.build_title("athletix", "Men", "athletic shorts", ["dry-fit", "pockets"], 2)
    assert t == "Athletix Men's Athletic Shorts, Dry-Fit, Pockets (Pack of 2)"


def test_shouting_detection():
    assert N.is_shouting("ATHLETIX MENS RUNNING SHORTS 7 INCH")
    assert not N.is_shouting("Men's Running Shorts")
