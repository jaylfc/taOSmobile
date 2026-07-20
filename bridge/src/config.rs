//! Bridge configuration.
//!
//! The config file lives on the device only — it holds the controller URL and
//! the device token, neither of which belong in the repo.

use anyhow::{Context, Result};
use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize, PartialEq, Eq)]
pub struct Config {
    /// WebSocket URL of the taOS controller, e.g. `ws://host:port/ws/worker`.
    pub controller_url: String,
    /// Bearer token identifying this device to the controller.
    pub device_token: String,
}

impl Config {
    /// Read config from `$XDG_CONFIG_HOME/taosd-bridge/config.toml`, falling
    /// back to `~/.config/taosd-bridge/config.toml`.
    pub fn load() -> Result<Self> {
        let path = Self::default_path()?;
        Self::from_file(&path)
    }

    pub fn from_file(path: &Path) -> Result<Self> {
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("reading config at {}", path.display()))?;
        Self::from_str(&text)
    }

    pub fn from_str(text: &str) -> Result<Self> {
        toml::from_str(text).context("parsing config TOML")
    }

    pub fn default_path() -> Result<PathBuf> {
        let base = match std::env::var_os("XDG_CONFIG_HOME") {
            Some(dir) if !dir.is_empty() => PathBuf::from(dir),
            _ => {
                let home = std::env::var_os("HOME").context("HOME is not set")?;
                PathBuf::from(home).join(".config")
            }
        };
        Ok(base.join("taosd-bridge").join("config.toml"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_complete_config() {
        let cfg = Config::from_str(
            r#"
            controller_url = "ws://controller.local:8080/ws/worker"
            device_token = "secret-token"
            "#,
        )
        .expect("should parse");

        assert_eq!(cfg.controller_url, "ws://controller.local:8080/ws/worker");
        assert_eq!(cfg.device_token, "secret-token");
    }

    #[test]
    fn rejects_config_missing_a_field() {
        let err = Config::from_str(r#"controller_url = "ws://x/ws""#)
            .expect_err("missing device_token should fail");
        assert!(
            err.to_string().contains("parsing config TOML"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn reads_from_a_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(
            &path,
            "controller_url = \"ws://h/ws\"\ndevice_token = \"t\"\n",
        )
        .unwrap();

        let cfg = Config::from_file(&path).expect("should load");
        assert_eq!(cfg.device_token, "t");
    }

    #[test]
    fn missing_file_reports_the_path() {
        let err = Config::from_file(Path::new("/nonexistent/taosd-bridge.toml"))
            .expect_err("missing file should fail");
        assert!(err.to_string().contains("/nonexistent/taosd-bridge.toml"));
    }

    #[test]
    fn default_path_prefers_xdg_config_home() {
        // Serialised implicitly: this test owns the env var it sets.
        temp_env_var("XDG_CONFIG_HOME", Some("/xdg"), || {
            assert_eq!(
                Config::default_path().unwrap(),
                PathBuf::from("/xdg/taosd-bridge/config.toml")
            );
        });
    }

    #[test]
    fn default_path_falls_back_to_home() {
        temp_env_var("XDG_CONFIG_HOME", None, || {
            temp_env_var("HOME", Some("/home/phablet"), || {
                assert_eq!(
                    Config::default_path().unwrap(),
                    PathBuf::from("/home/phablet/.config/taosd-bridge/config.toml")
                );
            });
        });
    }

    /// Set (or unset) an env var for the duration of `f`, restoring it after.
    fn temp_env_var<F: FnOnce()>(key: &str, value: Option<&str>, f: F) {
        let previous = std::env::var_os(key);
        match value {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
        f();
        match previous {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }
}
