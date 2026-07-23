from app.bot.telegram import (
    DealLookup,
    QUICK_QUERIES,
    cancel_keyboard,
    main_menu_keyboard,
)


def test_main_menu_contains_quick_actions() -> None:
    keyboard = main_menu_keyboard()
    labels = [
        button.text
        for row in keyboard.keyboard
        for button in row
    ]

    assert keyboard.is_persistent is True
    assert set(QUICK_QUERIES) <= set(labels)
    assert "🔎 Сделка по ID" in labels
    assert "🧹 Очистить диалог" in labels
    assert "ℹ️ Помощь" in labels


def test_deal_lookup_has_cancel_button_and_state() -> None:
    keyboard = cancel_keyboard()

    assert keyboard.keyboard[0][0].text == "⬅️ Отмена"
    assert DealLookup.waiting_for_id.state.endswith("waiting_for_id")
