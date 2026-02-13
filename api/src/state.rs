use sqlx::PgPool;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SignupGate {
    Invite,
    Open,
    Payment,
}

impl SignupGate {
    pub fn from_env() -> Self {
        match std::env::var("SIGNUP_GATE")
            .unwrap_or_default()
            .to_lowercase()
            .as_str()
        {
            "invite" => Self::Invite,
            "payment" => Self::Payment,
            _ => Self::Open,
        }
    }
}

#[derive(Clone)]
pub struct AppState {
    pub db: PgPool,
    pub signup_gate: SignupGate,
}
