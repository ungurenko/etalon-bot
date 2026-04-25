import importlib
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LlmMenuStyleTests(unittest.TestCase):
    def test_default_openrouter_model_is_gemini_3_flash(self):
        old_bot_token = os.environ.get("BOT_TOKEN")
        old_admin_ids = os.environ.get("ADMIN_IDS")
        old_model = os.environ.get("OPENROUTER_MODEL")

        os.environ["BOT_TOKEN"] = "test-token"
        os.environ["ADMIN_IDS"] = "1"
        os.environ.pop("OPENROUTER_MODEL", None)

        try:
            import etalon_bot.config as config

            config = importlib.reload(config)

            self.assertEqual(
                config.OPENROUTER_MODEL,
                "google/gemini-3-flash-preview",
            )
        finally:
            if old_bot_token is None:
                os.environ.pop("BOT_TOKEN", None)
            else:
                os.environ["BOT_TOKEN"] = old_bot_token
            if old_admin_ids is None:
                os.environ.pop("ADMIN_IDS", None)
            else:
                os.environ["ADMIN_IDS"] = old_admin_ids
            if old_model is None:
                os.environ.pop("OPENROUTER_MODEL", None)
            else:
                os.environ["OPENROUTER_MODEL"] = old_model

    def test_llm_output_replaces_long_dashes(self):
        from etalon_bot.services.llm_service import _sanitize_llm_output

        self.assertEqual(_sanitize_llm_output("Раз — два – три"), "Раз - два - три")

    def test_etalon_voice_prompt_limits_memory_style(self):
        source = (ROOT / "etalon_bot" / "services" / "context_builder.py").read_text()

        self.assertIn("примерно в каждом 4-м ответе", source)
        self.assertNotIn("От первого лица: «я знаю это состояние»", source)

    def test_menu_router_registered_before_chat_router(self):
        source = (ROOT / "etalon_bot" / "bot.py").read_text()

        commands_pos = source.index("dp.include_router(commands_router)")
        chat_pos = source.index("dp.include_router(chat_router)")

        self.assertLess(commands_pos, chat_pos)


if __name__ == "__main__":
    unittest.main()
