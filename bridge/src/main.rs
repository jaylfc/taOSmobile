//! taosd-bridge — exposes Ubuntu Touch phone hardware (SMS, dialing, battery)
//! to a taOS cluster as worker capabilities.

mod config;
mod protocol;

use anyhow::Result;
use config::Config;
use tracing::info;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "taosd_bridge=info".into()),
        )
        .init();

    let config = Config::load()?;
    info!(controller = %config.controller_url, "config loaded");

    // Providers and uplink land in tasks 4-7; see the implementation plan.
    info!(
        capabilities = ?protocol::CAPABILITIES,
        "bridge scaffold running; uplink not yet wired"
    );

    Ok(())
}
