from pathlib import Path

from PIL import Image

from vt.render import fit_text, render_caption_png, render_text_png, uniform_font_size


def test_fit_text_shrinks_and_wraps():
    fit = fit_text("Add 1 tbsp of soy sauce and mix well", 300, 80)
    assert not fit["overflow"]
    assert 1 <= len(fit["lines"]) <= 2


def test_fit_text_overflow_flag():
    fit = fit_text("This sentence is far too long to fit into such a tiny caption box", 60, 20)
    assert fit["overflow"]


def test_render_stays_inside_frame(tmp_path):
    place = render_text_png("Cut the onion", [900, 10, 1080, 70], tmp_path / "a.png", (1080, 1920))
    assert place["x"] >= 0 and place["x"] + place["w"] <= 1080
    assert (tmp_path / "a.png").exists()


def test_uniform_size_fits_longest_caption_in_two_lines():
    frame = (640, 360)
    items = [("Fluffy Homemade Focaccia", [160, 14, 472, 53]),
             ("Add 3g dry yeast to 300g bread flour", [108, 299, 529, 337]),
             ("Bake in a 200°C oven for 20 minutes and it's done!", [134, 298, 485, 341])]
    size = uniform_font_size(items, frame)
    assert 14 <= size <= int(360 * 0.055)
    for text, box in items:
        place = render_caption_png(text, box, Path("/tmp/_cap.png"), frame, size)
        assert place["size"] == size and not place["overflow"] and len(place["lines"]) <= 2
        assert 0 <= place["x"] and place["x"] + place["w"] <= 640


def test_caption_background_is_transparent(tmp_path):
    render_caption_png("Knead for 10 minutes", [132, 299, 505, 339], tmp_path / "c.png", (640, 360), 19)
    img = Image.open(tmp_path / "c.png")
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0
