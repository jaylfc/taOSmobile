//! Wire protocol between the bridge and the taOS controller.
//!
//! The message shapes here are normative — they are the interface contract in
//! `docs/superpowers/specs/2026-07-20-taosmobile-demo-design.md`. The tests
//! pin the exact JSON; change them only alongside the spec and taOS-dev.
//!
//! Open question for taOS-dev: the existing worker protocol may use a flat
//! envelope (`{"type": ..., ...fields}`) rather than the `{"type", "payload"}`
//! framing used here. If so, adjust `ENVELOPE` handling and these tests to
//! match the server — the server is authoritative.

use serde::{Deserialize, Serialize};

/// Capability strings this device advertises to the cluster.
pub const CAPABILITIES: [&str; 3] = ["sms", "dial", "battery"];

/// Registration payload sent immediately after the socket opens.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Registration {
    pub platform: String,
    pub tier_id: String,
    pub capabilities: Vec<String>,
    pub hardware: Hardware,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Hardware {
    pub model: String,
    pub soc: String,
    pub ram_gb: u32,
    pub sim: bool,
}

impl Registration {
    /// Registration for the Nothing Phone (1) demo device.
    pub fn nothing_phone_1() -> Self {
        Self {
            platform: "ubuntu-touch".to_string(),
            tier_id: "arm-snapdragon-12gb".to_string(),
            capabilities: CAPABILITIES.iter().map(|s| s.to_string()).collect(),
            hardware: Hardware {
                model: "Nothing Phone (1)".to_string(),
                soc: "SM7325".to_string(),
                ram_gb: 12,
                sim: true,
            },
        }
    }
}

/// Messages the bridge sends to the controller.
#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(tag = "type", content = "payload")]
pub enum Outbound {
    #[serde(rename = "worker.register")]
    Register(Registration),

    #[serde(rename = "sms.incoming")]
    SmsIncoming {
        from: String,
        body: String,
        /// UTC ISO8601.
        received_at: String,
    },

    #[serde(rename = "sms.send_result")]
    SmsSendResult {
        client_ref: String,
        ok: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    #[serde(rename = "call.dial_result")]
    CallDialResult {
        client_ref: String,
        ok: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },

    #[serde(rename = "battery.status")]
    BatteryStatus { percent: u8, charging: bool },
}

impl Outbound {
    pub fn send_ok(client_ref: impl Into<String>) -> Self {
        Outbound::SmsSendResult {
            client_ref: client_ref.into(),
            ok: true,
            error: None,
        }
    }

    pub fn send_failed(client_ref: impl Into<String>, error: impl Into<String>) -> Self {
        Outbound::SmsSendResult {
            client_ref: client_ref.into(),
            ok: false,
            error: Some(error.into()),
        }
    }

    pub fn dial_ok(client_ref: impl Into<String>) -> Self {
        Outbound::CallDialResult {
            client_ref: client_ref.into(),
            ok: true,
            error: None,
        }
    }

    pub fn dial_failed(client_ref: impl Into<String>, error: impl Into<String>) -> Self {
        Outbound::CallDialResult {
            client_ref: client_ref.into(),
            ok: false,
            error: Some(error.into()),
        }
    }
}

/// Messages the controller sends to the bridge.
///
/// Unknown message types become [`Inbound::Unknown`] rather than an error, so
/// a newer controller cannot break an older bridge.
#[derive(Debug, PartialEq, Eq)]
pub enum Inbound {
    SmsSend {
        client_ref: String,
        to: String,
        body: String,
    },

    CallDial {
        client_ref: String,
        number: String,
    },

    /// A message type this bridge does not implement. Carries the type name
    /// for logging.
    Unknown(String),
}

/// The adjacently-tagged envelope every controller message arrives in.
///
/// Parsed in two steps rather than via `#[serde(other)]`, which only supports
/// unit variants and so cannot absorb an unknown message's payload.
#[derive(Deserialize)]
struct Envelope {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    payload: serde_json::Value,
}

#[derive(Deserialize)]
struct SmsSendPayload {
    client_ref: String,
    to: String,
    body: String,
}

#[derive(Deserialize)]
struct CallDialPayload {
    client_ref: String,
    number: String,
}

impl Inbound {
    /// Parse a controller message. Errors only on malformed JSON or a known
    /// message type with a malformed payload — never on an unknown type.
    pub fn parse(text: &str) -> Result<Self, serde_json::Error> {
        let envelope: Envelope = serde_json::from_str(text)?;
        Self::from_envelope(envelope)
    }

    pub fn from_value(value: serde_json::Value) -> Result<Self, serde_json::Error> {
        let envelope: Envelope = serde_json::from_value(value)?;
        Self::from_envelope(envelope)
    }

    fn from_envelope(envelope: Envelope) -> Result<Self, serde_json::Error> {
        match envelope.kind.as_str() {
            "sms.send" => {
                let p: SmsSendPayload = serde_json::from_value(envelope.payload)?;
                Ok(Inbound::SmsSend {
                    client_ref: p.client_ref,
                    to: p.to,
                    body: p.body,
                })
            }
            "call.dial" => {
                let p: CallDialPayload = serde_json::from_value(envelope.payload)?;
                Ok(Inbound::CallDial {
                    client_ref: p.client_ref,
                    number: p.number,
                })
            }
            other => Ok(Inbound::Unknown(other.to_string())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn to_value(msg: &Outbound) -> serde_json::Value {
        serde_json::to_value(msg).expect("serializes")
    }

    #[test]
    fn registration_matches_the_contract() {
        assert_eq!(
            to_value(&Outbound::Register(Registration::nothing_phone_1())),
            json!({
                "type": "worker.register",
                "payload": {
                    "platform": "ubuntu-touch",
                    "tier_id": "arm-snapdragon-12gb",
                    "capabilities": ["sms", "dial", "battery"],
                    "hardware": {
                        "model": "Nothing Phone (1)",
                        "soc": "SM7325",
                        "ram_gb": 12,
                        "sim": true
                    }
                }
            })
        );
    }

    #[test]
    fn sms_incoming_matches_the_contract() {
        let msg = Outbound::SmsIncoming {
            from: "+447700900123".to_string(),
            body: "hello from the cluster".to_string(),
            received_at: "2026-07-20T15:04:05Z".to_string(),
        };
        assert_eq!(
            to_value(&msg),
            json!({
                "type": "sms.incoming",
                "payload": {
                    "from": "+447700900123",
                    "body": "hello from the cluster",
                    "received_at": "2026-07-20T15:04:05Z"
                }
            })
        );
    }

    #[test]
    fn successful_results_omit_the_error_field() {
        assert_eq!(
            to_value(&Outbound::send_ok("ref-1")),
            json!({
                "type": "sms.send_result",
                "payload": { "client_ref": "ref-1", "ok": true }
            })
        );
        assert_eq!(
            to_value(&Outbound::dial_ok("ref-2")),
            json!({
                "type": "call.dial_result",
                "payload": { "client_ref": "ref-2", "ok": true }
            })
        );
    }

    #[test]
    fn failed_results_carry_the_error_string() {
        assert_eq!(
            to_value(&Outbound::send_failed("ref-3", "modem offline")),
            json!({
                "type": "sms.send_result",
                "payload": { "client_ref": "ref-3", "ok": false, "error": "modem offline" }
            })
        );
        assert_eq!(
            to_value(&Outbound::dial_failed("ref-4", "no telephony service")),
            json!({
                "type": "call.dial_result",
                "payload": { "client_ref": "ref-4", "ok": false, "error": "no telephony service" }
            })
        );
    }

    #[test]
    fn battery_status_matches_the_contract() {
        assert_eq!(
            to_value(&Outbound::BatteryStatus {
                percent: 87,
                charging: true
            }),
            json!({
                "type": "battery.status",
                "payload": { "percent": 87, "charging": true }
            })
        );
    }

    #[test]
    fn parses_sms_send() {
        let msg = Inbound::from_value(json!({
            "type": "sms.send",
            "payload": { "client_ref": "r1", "to": "+447700900123", "body": "reply text" }
        }))
        .expect("parses");

        assert_eq!(
            msg,
            Inbound::SmsSend {
                client_ref: "r1".to_string(),
                to: "+447700900123".to_string(),
                body: "reply text".to_string(),
            }
        );
    }

    #[test]
    fn parses_call_dial() {
        let msg = Inbound::from_value(json!({
            "type": "call.dial",
            "payload": { "client_ref": "r2", "number": "+447700900123" }
        }))
        .expect("parses");

        assert_eq!(
            msg,
            Inbound::CallDial {
                client_ref: "r2".to_string(),
                number: "+447700900123".to_string(),
            }
        );
    }

    #[test]
    fn parses_from_raw_text() {
        let msg = Inbound::parse(
            r#"{"type":"call.dial","payload":{"client_ref":"r3","number":"+15551234567"}}"#,
        )
        .expect("parses");

        assert_eq!(
            msg,
            Inbound::CallDial {
                client_ref: "r3".to_string(),
                number: "+15551234567".to_string(),
            }
        );
    }

    #[test]
    fn unknown_message_types_are_ignored_not_errors() {
        let msg = Inbound::from_value(json!({
            "type": "some.future.capability",
            "payload": { "anything": 1 }
        }))
        .expect("unknown types must parse");

        assert_eq!(msg, Inbound::Unknown("some.future.capability".to_string()));
    }

    #[test]
    fn unknown_message_without_a_payload_still_parses() {
        let msg = Inbound::from_value(json!({ "type": "ping" })).expect("parses");
        assert_eq!(msg, Inbound::Unknown("ping".to_string()));
    }

    #[test]
    fn known_type_with_a_malformed_payload_is_an_error() {
        let err = Inbound::from_value(json!({
            "type": "sms.send",
            "payload": { "client_ref": "r1" }
        }))
        .expect_err("missing to/body should fail");

        assert!(err.to_string().contains("missing field"), "got: {err}");
    }

    #[test]
    fn malformed_json_is_an_error() {
        Inbound::parse("{not json").expect_err("should fail");
    }
}
