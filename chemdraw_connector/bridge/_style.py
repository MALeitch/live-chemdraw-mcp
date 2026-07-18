"""Journal style presets."""
from ..domain.style_presets import PRESETS, get_preset
from ._plumbing import SLOW_TIMEOUT


class _Style:
    def apply_style_preset(self, preset="ACS 1996"):
        values = get_preset(preset)

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            settings = doc.Settings
            applied, skipped = {}, {}
            for key, value in values.items():
                try:
                    setattr(settings, key, value)
                    applied[key] = value
                except Exception as exc:
                    skipped[key] = str(exc)
            try:
                settings.ApplySettings()
                restyled = True
            except Exception:
                restyled = False
            return {
                "preset": preset,
                "applied": applied,
                "skipped": skipped,
                "restyled_existing_objects": restyled,
                "backup_path": backup,
            }
        return self._run(go, timeout=SLOW_TIMEOUT)

    @staticmethod
    def available_presets():
        return sorted(PRESETS)
