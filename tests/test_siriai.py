import pytest

from phoneuse import siriai


def test_siri_reply_requires_a_submitted_question_and_skips_old_text():
    prompt = "How many inches are in a foot?"
    draft = (0.81, {"old conversation", "how many inches are in a foot?"})
    assert not siriai.siri_reply([(0.5, 0.81, prompt), (0.5, 0.9, "Space")], prompt, draft)
    question = [(0.5, 0.26, "How many inches"), (0.5, 0.30, "are in a foot?")]
    assert not siriai.siri_reply(question + [(0.4, 0.4, "Thinking…")], prompt, draft)
    assert siriai.siri_reply(question + [(0.5, 0.39, "Old conversation"),
                                        (0.5, 0.43, "There are 12 inches in a foot."),
                                        (0.5, 0.62, "Ask Siri"), (0.5, 0.72, "R"),
                                        (0.5, 0.83, "I'm")], prompt, draft) == "There are 12 inches in a foot."


# On a 440 x 956 point screen: the play button of a left-column tile named at y 0.30, and of a
# summary page whose heading is at y 0.290
PLAY = (185 / 440, (0.30 * 956 - 53.5) / 956)
SUMMARY_PLAY = (341 / 440, (0.290 * 956 - 62) / 956)
HEADING = (0.27, 0.147, "All Shortcuts")
GRID_SEARCH = (0.16, 0.20, "Q Search")
FOCUSED_SEARCH = (0.18, 0.085, "Search")


def phone(monkeypatch, frames, start=()):
    """Each frame is read three times, so waits that need a settled screen see it hold. `start` is the
    screen before the run (by default the home screen, read once), which decides whether the app opens."""
    frames = iter([list(start)] + [frame for frame in frames for _ in range(3)])
    actions = []
    monkeypatch.setattr(siriai.orientation, "current_points", lambda: (440, 956))
    monkeypatch.setattr(siriai, "picture_ready", lambda: True)
    monkeypatch.setattr(siriai, "open_app", lambda h, name: actions.append(name))
    monkeypatch.setattr(siriai, "tap", lambda h, pos: actions.append(pos))
    monkeypatch.setattr(siriai.cli, "screen_text", lambda: next(frames))
    monkeypatch.setattr(siriai.cli, "refuse_if_locked", lambda: None)
    monkeypatch.setattr(siriai.cli, "key_combo", lambda h, key: actions.append(key))
    monkeypatch.setattr(siriai.cli, "type_text", lambda h, text: actions.append(text))
    return actions


def model_frames(prompt):
    return [
        [(0.5, 0.32, "PhoneUse model prompt")],
        [(0.5, 0.32, "PhoneUse model prompt"), (0.5, 0.38, prompt), (0.64, 0.43, "Done")],
        [(0.15, 0.25, "Other shortcut"), (0.5, 0.32, "PhoneUse model answer"),
         (0.5, 0.39, "A new title"), (0.50, 0.47, "OK")],
    ]


def test_on_device_taps_its_tile_and_submits_only_from_named_dialog(monkeypatch):
    prompt = "Rewrite this"
    actions = phone(monkeypatch, [[HEADING, GRID_SEARCH, (0.3, 0.30, "PhoneUse On-Device")]] + model_frames(prompt))
    assert siriai.ask(object(), prompt, "on-device", timeout=3) == "A new title"
    assert actions == ["Shortcuts", PLAY, "cmd+a", prompt, (0.64, 0.43)]


def test_summary_page_runs_the_shortcut_with_its_play_button(monkeypatch):
    prompt = "Rewrite this"
    summary = [(0.47, 0.088, "PhoneUse On-Device"), (0.33, 0.290, "This shortcut will:"),
               (0.37, 0.331, "Ask for text input")]
    actions = phone(monkeypatch, [[HEADING, GRID_SEARCH, (0.3, 0.30, "PhoneUse On-Device")], summary]
                    + model_frames(prompt))
    assert siriai.ask(object(), prompt, "on-device", timeout=3) == "A new title"
    assert actions == ["Shortcuts", PLAY, SUMMARY_PLAY, "cmd+a", prompt, (0.64, 0.43)]


def test_reopened_summary_runs_its_own_shortcut_or_goes_back_from_another(monkeypatch):
    def summary(title):
        return [(0.47, 0.088, title), (0.89, 0.088, "Edit"), (0.33, 0.290, "This shortcut will:")]
    prompt = "Rewrite this"
    actions = phone(monkeypatch, [summary("PhoneUse On-Device")] + model_frames(prompt))
    assert siriai.ask(object(), prompt, "on-device", timeout=3) == "A new title"
    assert actions[:2] == ["Shortcuts", SUMMARY_PLAY]
    actions = phone(monkeypatch, [summary("PhoneUse Cloud"), [HEADING, GRID_SEARCH, (0.3, 0.30, "PhoneUse On-Device")],
                                  summary("PhoneUse On-Device")] + model_frames(prompt))
    assert siriai.ask(object(), prompt, "on-device", timeout=3) == "A new title"
    assert actions[:4] == ["Shortcuts", siriai.back_button(), PLAY, SUMMARY_PLAY]


def test_app_already_in_front_is_not_opened_again(monkeypatch):
    prompt = "Rewrite this"
    grid = [(0.27, 0.147, "All Shortcuts"), (0.3, 0.30, "PhoneUse On-Device")]
    actions = phone(monkeypatch, [grid] + model_frames(prompt), start=grid)
    assert siriai.ask(object(), prompt, "on-device", timeout=3) == "A new title"
    assert actions[0] == PLAY


def test_spotlight_or_another_app_is_not_taken_for_the_app():
    spotlight = [(0.3, 0.09, "Search or Ask"), (0.2, 0.2, "Top Hit"), (0.28, 0.611, "Ask Siri")]
    assert not siriai.already_open(spotlight, False)
    assert not siriai.already_open([(0.5, 0.1, "Settings")], True)
    assert siriai.already_open([(0.28, 0.611, "Ask Siri")], False)


def test_button_positions_keep_their_point_margins_on_a_smaller_phone(monkeypatch):
    monkeypatch.setattr(siriai.orientation, "current_points", lambda: (440, 956))
    assert siriai.tile_play([], (0.3, 0.30)) == pytest.approx((0.420, 0.244), abs=0.001)
    assert siriai.tile_play([], (0.7, 0.30)) == pytest.approx((0.886, 0.244), abs=0.001)
    monkeypatch.setattr(siriai.orientation, "current_points", lambda: (393, 852))  # iPhone 15 Pro
    x, y = siriai.tile_play([], (0.7, 0.30))
    assert (x * 393, 0.30 * 852 - y * 852) == pytest.approx((393 - 50, 53.5))
    x, _ = siriai.summary_play([(0.33, 0.3, "This shortcut will:")])
    assert x * 393 == pytest.approx(393 - 99)


def test_two_line_tile_name_uses_its_bottom_line(monkeypatch):
    monkeypatch.setattr(siriai.orientation, "current_points", lambda: (440, 956))
    lines = [(0.20, 0.244, "Ask On-Device"), (0.18, 0.266, "Intelligence"), (0.66, 0.265, "New Shortcut")]
    assert siriai.tile_play(lines, (0.20, 0.244))[1] == pytest.approx(0.266 - 53.5 / 956)


def test_cloud_searches_the_app_and_never_picks_cloud_pro(monkeypatch):
    prompt = "Plan a trip"
    actions = phone(monkeypatch, [
        [HEADING, GRID_SEARCH, (0.3, 0.30, "Ask On-Device Intelligence")],
        [FOCUSED_SEARCH],
        [(0.3, 0.085, "Q PhoneUse Cloud"), (0.2, 0.19, "PhoneUse Cloud Pro"), (0.7, 0.19, "PhoneUse Cloud")],
    ] + model_frames(prompt))
    assert siriai.ask(object(), prompt, "cloud", timeout=3) == "A new title"
    assert actions == ["Shortcuts", GRID_SEARCH[:2], "PhoneUse Cloud", (0.7, 0.19),
                       "cmd+a", prompt, (0.64, 0.43)]


def test_siri_starts_a_new_conversation_from_the_conversations_list(monkeypatch):
    prompt = "Ounces in a pound"
    keyboard = [(0.057 + 0.098 * i, 0.72, c) for i, c in enumerate("QWERTYUIOP")] + [(0.5, 0.83, "Z")]
    actions = phone(monkeypatch, [
        [(0.5, 0.088, "Conversations"), (0.4, 0.2, "An old chat"), (0.13, 0.938, "Q"), (0.87, 0.937, "0")],
        [(0.28, 0.611, "Ask Siri")] + keyboard,
        [(0.28, 0.611, prompt)] + keyboard,
    ] + [[(0.5, 0.26, prompt), (0.5, 0.40, "There are 16 ounces in a pound."), (0.28, 0.611, "Ask Siri")]
         + keyboard] * 3)  # the reply must hold still for 1.2 s
    monkeypatch.setattr(siriai.cli, "acted", lambda seq: None)
    h = type("Hid", (), {"first_seq": None})()
    assert siriai.ask(h, prompt, siriai.SIRI, timeout=4) == "There are 16 ounces in a pound."
    assert actions == ["Siri", (0.87, 0.937), (0.28, 0.611), "cmd+a", prompt, "enter"]


def test_siri_composer_with_old_text_is_found_but_keyboard_rows_are_not():
    keyboard = [(0.057 + 0.098 * i, 0.717, c) for i, c in enumerate("qwertyuiop")]
    draft = [(0.495, 0.593, "What is 8 times 8? Answer with the"), (0.099, 0.612, "十"),
             (0.324, 0.612, "number only.l"), (0.895, 0.611, "↑"), (0.501, 0.663, "If")]
    keyboard += [(0.131, 0.889, "123"), (0.870, 0.887, "乙")]
    assert siriai.siri_ready(draft + keyboard) == ("field", (0.5, 0.612))
    no_arrow = [line for line in draft if line[2] != "↑"]
    assert siriai.siri_ready(no_arrow + keyboard) == ("field", (0.5, 0.612))
    assert not siriai.siri_ready(keyboard)
    conversations = [(0.5, 0.088, "Conversations"), (0.13, 0.938, "Q"), (0.87, 0.937, "0")]
    assert siriai.siri_ready(conversations) == ("compose", (0.87, 0.937))


def test_wrapped_siri_draft_with_cursor_and_buttons_matches():
    prompt = "What is 8 times 8? Answer with the number only."
    lines = [(0.495, 0.593, "What is 8 times 8? Answer with the"), (0.099, 0.612, "十"),
             (0.324, 0.612, "number only.l"), (0.895, 0.611, "↑")]
    composer = [line for line in lines if 0.15 < line[0] < 0.85]
    assert list(siriai.matching_prompt(composer, prompt)) == [(0, 1)]
    assert not list(siriai.matching_prompt(composer, prompt.replace("8?", "9?")))


def test_quiet_wait_ignores_ocr_position_wobble(monkeypatch):
    reads = iter([[(0.28, 0.611, "Ask Siri")], [(0.279, 0.612, "Ask Siri")]] * 5)
    monkeypatch.setattr(siriai, "picture_ready", lambda: True)
    monkeypatch.setattr(siriai.cli, "screen_text", lambda: next(reads))
    assert siriai.wait_for(siriai.siri_ready, 3, "never settled", quiet=0.5)[0] == "field"


def test_conversations_list_without_a_lone_compose_button_is_not_guessed():
    assert not siriai.siri_ready([(0.5, 0.088, "Conversations"), (0.8, 0.93, "Edit"), (0.9, 0.94, "0")])


def test_missing_shortcut_explains_how_to_make_it(monkeypatch, capsys):
    phone(monkeypatch, [[HEADING, GRID_SEARCH], [FOCUSED_SEARCH]] + [[(0.3, 0.085, "Q PhoneUse Cloud Pro")]] * 30)
    with pytest.raises(SystemExit, match="1"):
        siriai.ask(object(), "hi", "cloud-pro", timeout=3)
    err = capsys.readouterr().err
    assert "Name: PhoneUse Cloud Pro" in err and 'type "PhoneUse Cloud Pro"' in err
    assert "Model Cloud Pro," in err and 'Prompt "phoneuse model prompt"' in err
    assert "to the Cloud Pro model" in err
    assert 'Title "phoneuse model answer"' in err and "--siri-plus-context" in err


def test_short_list_without_search_field_explains_how_to_make_it(monkeypatch, capsys):
    phone(monkeypatch, [[(0.86, 0.088, "Select"), (0.27, 0.147, "All Shortcuts"),
                         (0.2, 0.244, "Ask On-Device"), (0.66, 0.265, "New Shortcut")]] * 3)
    with pytest.raises(SystemExit, match="1"):
        siriai.ask(object(), "hi", "on-device", timeout=3)
    assert "Name: PhoneUse On-Device" in capsys.readouterr().err


@pytest.mark.parametrize("model, ok", [("iPhone19,3", True), ("iPhone16,1", True), ("iPhone17,5", True),
                                       ("iPhone15,4", False), ("iPhone14,7", False), ("iPad16,3", False),
                                       ("", False), (None, False)])
def test_apple_intelligence_needs_iphone_15_pro_or_later(model, ok):
    assert siriai.apple_intelligence(model) is ok


def test_older_iphone_is_refused_before_any_input(monkeypatch, capsys):
    monkeypatch.setattr(siriai, "picture_ready", lambda: True)
    monkeypatch.setattr(siriai.cli, "IS_WINDOWS", False)
    monkeypatch.setattr(siriai.phones, "current", lambda: "iPhone 14")
    monkeypatch.setattr(siriai.phones, "entry", lambda name: {"model": "iPhone14,7"})
    monkeypatch.setattr(siriai, "ask", lambda *a: pytest.fail("Sent input"))
    with pytest.raises(SystemExit, match="1"):
        siriai.run(["--cloud", "hello"])
    assert "iPhone 15 Pro or later" in capsys.readouterr().err


def test_editor_action_search_is_never_used(monkeypatch):
    editor = [(0.10, 0.106, "Q"), (0.18, 0.106, "Search"), (0.19, 0.166, "Automation"), (0.5, 0.166, "Scripting")]
    actions = phone(monkeypatch, [editor] * 20)
    with pytest.raises(SystemExit, match="1"):
        siriai.ask(object(), "hi", "on-device", timeout=3)
    assert actions == ["Shortcuts"]


def test_no_model_flag_lists_every_mode_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr(siriai, "picture_ready", lambda: pytest.fail("Connected to the phone"))
    with pytest.raises(SystemExit, match="1"):
        siriai.run(["hello"])
    err = capsys.readouterr().err
    assert all(f"--{mode}:" in err for mode in siriai.MODES)


def test_one_model_flag_at_most(monkeypatch):
    monkeypatch.setattr(siriai, "picture_ready", lambda: pytest.fail("Connected to the phone"))
    with pytest.raises(SystemExit, match="2"):
        siriai.run(["--cloud", "--on-device", "hello"])


def test_mcp_model_enum_becomes_the_model_option():
    from phoneuse.commands import TOOLS
    command = TOOLS["siriai"]
    assert command.properties["model"]["enum"] == list(siriai.MODES)
    assert command.arguments({"prompt": "hi", "model": "cloud"}) == ["siriai", "--model=cloud", "hi"]


def test_short_left_aligned_answer_is_read():
    lines = [(0.312, 0.100, "phoneuse model answer"), (0.115, 0.125, "144"), (0.501, 0.181, "OK"),
             (0.328, 0.291, "This shortcut will:")]
    assert [text for _, _, text in siriai.alert_body(lines, siriai.OUTPUT)] == ["144"]


def test_shortcut_dialog_with_ambiguous_done_is_not_read():
    lines = [(0.5, 0.32, "PhoneUse model answer"), (0.5, 0.39, "A new title"),
             (0.5, 0.47, "Done"), (0.5, 0.52, "Done")]
    assert not siriai.alert_body(lines, siriai.OUTPUT)


@pytest.mark.parametrize("prompt", ["", "hello\nworld", r"hello\nworld", "caf\N{LATIN SMALL LETTER E WITH ACUTE}"])
def test_rejects_unsupported_prompts_before_connecting(monkeypatch, prompt):
    monkeypatch.setattr(siriai, "picture_ready", lambda: pytest.fail("Connected to the phone"))
    with pytest.raises(SystemExit, match="2"):
        siriai.run([prompt])


# Read on an iPhone 17 Pro (2026-11-25): the Siri composer with a draft and the keyboard up. OCR read the
# send arrow ("个") but not the + button at the row's left end.
KEYBOARD = [(0.059 + 0.0985 * i, 0.709, c) for i, c in enumerate("qWertyuiop")] + \
    [(0.106 + 0.0985 * i, 0.770, c) for i, c in enumerate("aSdfghjk")] + [(0.071, 0.893, "123")]
DRAFT_17PRO = [(0.141, 0.037, "9:51"), (0.338, 0.598, "Hello therel"), (0.892, 0.598, "个"),
               (0.499, 0.653, "the"), (0.833, 0.653, "they")] + KEYBOARD
CONVERSATIONS_17PRO = [(0.326, 0.160, "Conversations"), (0.211, 0.224, "Previous 30 Days"), (0.098, 0.277, "Notes"),
                       (0.212, 0.303, "Here are your notes."), (0.147, 0.710, "New Photo"), (0.855, 0.933, "0")]


def test_draft_with_only_the_send_arrow_read():
    from phoneuse import siriai
    assert siriai.keyboard_up(DRAFT_17PRO)
    assert siriai.siri_draft(DRAFT_17PRO) == (0.5, 0.598)
    no_keyboard = [line for line in DRAFT_17PRO if line not in KEYBOARD]
    assert siriai.siri_draft(no_keyboard) is None  # a reply's line ending in a short token doesn't pass


def test_conversations_header_lower_on_a_17_pro():
    from phoneuse import siriai
    assert siriai.siri_ready(CONVERSATIONS_17PRO) == ("compose", (0.855, 0.933))
    assert siriai.siri_draft(CONVERSATIONS_17PRO) is None


def test_library_page_opens_all_shortcuts_and_wrapped_names_match_their_own_tile(monkeypatch):
    # Read on an iPhone 17 Pro on 2026-11-25: Shortcuts opened on Library, and the grid wrapped each name
    prompt = "Rewrite this"
    library = [(0.74, 0.096, "Edit"), (0.18, 0.162, "Library"), (0.29, 0.222, "All Shortcuts"),
               (0.85, 0.222, "22"), (0.28, 0.281, "Share Sheet")]
    grid = [(0.86, 0.096, "Select"), (0.30, 0.161, "All Shortcuts"), (0.17, 0.217, "Q Search"),
            (0.180, 0.334, "PhoneUse"), (0.185, 0.358, "On-Device"), (0.716, 0.358, "PhoneUse Cloud"),
            (0.180, 0.474, "PhoneUse"), (0.695, 0.474, "PhoneUse On-"), (0.177, 0.499, "Cloud Pro"),
            (0.637, 0.499, "Device 2")]
    actions = phone(monkeypatch, [library, grid] + model_frames(prompt))
    assert siriai.ask(object(), prompt, "on-device", timeout=3) == "A new title"
    assert actions[:3] == ["Shortcuts", (0.29, 0.222), siriai.tile_play(grid, (0.180, 0.334))]
    named = siriai.tile_names(grid)
    assert siriai.target(named, "PhoneUse Cloud Pro") == (0.180, 0.474)
    assert siriai.target(named, "PhoneUse Cloud") == (0.716, 0.358)
