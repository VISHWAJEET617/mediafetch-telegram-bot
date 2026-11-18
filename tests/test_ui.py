from mediafetch.ui import Quality, forward_keyboard, quality_keyboard, start_keyboard


def test_start_is_blue() -> None:
    button = start_keyboard()["inline_keyboard"][0][0]
    assert button["style"] == "primary"


def test_quality_colors() -> None:
    markup = quality_keyboard("job123", [Quality(2160), Quality(1080), Quality(720)])
    buttons = [row[0] for row in markup["inline_keyboard"]]
    assert buttons[0]["style"] == "success"
    assert buttons[1]["style"] == "primary"
    assert "style" not in buttons[2]
    assert buttons[-1]["style"] == "danger"


def test_forward_opens_native_chat_picker() -> None:
    button = forward_keyboard("abc123")["inline_keyboard"][0][0]
    assert button["style"] == "success"
    picker = button["switch_inline_query_chosen_chat"]
    assert picker["allow_user_chats"] is True
    assert picker["allow_group_chats"] is True
    assert picker["allow_channel_chats"] is True
    assert picker["allow_bot_chats"] is False

