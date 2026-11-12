from vt.render import fit_text, render_text_png


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
