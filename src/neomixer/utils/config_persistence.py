import os
import json
import logging
import tempfile
import shutil

LOG = logging.getLogger("generic")


def import_legacy_config(config_file, legacy_file):
    '''
    Start from a Pulsemeeter config on first run, its format is a subset of ours
    Returns True if it was imported
    '''
    if os.path.exists(config_file) or not os.path.exists(legacy_file):
        return False

    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    shutil.copy2(legacy_file, config_file)
    LOG.info("Imported the Pulsemeeter config from %s", legacy_file)
    return True


class ConfigPersistence:
    '''
    Manages config state and persistence.
    '''
    def __init__(self, config_model_cls, config_file):
        self.config_file = config_file
        self.config_model_cls = config_model_cls
        self.first_run = not os.path.exists(config_file)
        self._config = self.load()
        self._last_saved = self._serialize()

    def load(self):
        '''
        Load config from file
        '''
        if not os.path.exists(self.config_file):
            return self.config_model_cls()

        try:
            with open(self.config_file, 'r', encoding='utf-8') as outfile:
                config = json.load(outfile)
            instance = self.config_model_cls(**config)
            return instance

        except (json.JSONDecodeError, ValueError) as e:
            LOG.error(f"Failed to load config from {self.config_file}: {e}")
            LOG.warning("Falling back to default configuration. Your config file may be corrupted.")
            self._keep_broken_config()

            # Fall back to default config
            return self.config_model_cls()

        except Exception as e:
            LOG.error(f"Unexpected error loading config: {e}")
            self._keep_broken_config()
            return self.config_model_cls()

    def _keep_broken_config(self):
        '''
        Autosave would overwrite an unreadable config with defaults, keep a copy to repair
        '''
        try:
            shutil.copy2(self.config_file, self.config_file + '.broken')
        except OSError as e:
            LOG.error(f"Could not keep a copy of the broken config: {e}")

    def _serialize(self):
        return json.dumps(self._config.model_dump(), indent='\t', separators=(',', ': '))

    def save_if_changed(self):
        '''
        Save when anything in the config changed since the last save. Comparing the whole
        config is what makes this reliable: no change can forget to mark itself as unsaved.
        Returns True if it saved.
        '''
        if self._serialize() == self._last_saved:
            return False

        self.save()
        return True

    def save(self):
        '''
        Write configuration to file using atomic write pattern
        '''
        config_dir = os.path.dirname(self.config_file)
        if not os.path.isdir(config_dir):
            os.makedirs(config_dir, exist_ok=True)

        LOG.debug("Writing config")

        try:
            # Write to temporary file in same directory (for atomic rename)
            fd, temp_path = tempfile.mkstemp(
                dir=config_dir,
                prefix='.config_',
                suffix='.json.tmp'
            )

            try:
                serialized = self._serialize()
                with os.fdopen(fd, 'w', encoding='utf-8') as outfile:
                    outfile.write(serialized)
                    outfile.flush()
                    os.fsync(outfile.fileno())

                # Atomic rename (only succeeds if write completed)
                # On POSIX systems, this is atomic - the file is replaced instantly
                # If the process crashes before this line, original config.json is untouched
                shutil.move(temp_path, self.config_file)
                self._last_saved = serialized
                LOG.debug("Config saved successfully")

            except Exception:
                # Clean up temp file on failure
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise

        except Exception as e:
            LOG.error(f"Failed to save config: {e}")
            raise

    def get_config(self):
        return self._config
