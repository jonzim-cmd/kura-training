use clap::{Subcommand, ValueEnum};
use serde_json::json;

use crate::util::api_request;

#[derive(Subcommand)]
pub enum AccessCommands {
    /// Submit a public access request
    Request {
        /// Contact email
        #[arg(long)]
        email: String,
        /// Optional display name
        #[arg(long)]
        name: Option<String>,
        /// Optional context for your request
        #[arg(long)]
        context: Option<String>,
        /// Optional locale (de|en|ja)
        #[arg(long)]
        locale: Option<AccessLocale>,
        /// Optional Turnstile token
        #[arg(long)]
        turnstile_token: Option<String>,
    },
}

#[derive(Copy, Clone, Debug, Eq, PartialEq, ValueEnum)]
pub enum AccessLocale {
    De,
    En,
    Ja,
}

impl AccessLocale {
    fn as_str(self) -> &'static str {
        match self {
            AccessLocale::De => "de",
            AccessLocale::En => "en",
            AccessLocale::Ja => "ja",
        }
    }
}

pub async fn run(api_url: &str, command: AccessCommands) -> i32 {
    match command {
        AccessCommands::Request {
            email,
            name,
            context,
            locale,
            turnstile_token,
        } => request(api_url, &email, name, context, locale, turnstile_token).await,
    }
}

async fn request(
    api_url: &str,
    email: &str,
    name: Option<String>,
    context: Option<String>,
    locale: Option<AccessLocale>,
    turnstile_token: Option<String>,
) -> i32 {
    let mut body = json!({
        "email": email,
    });

    if let Some(name) = name {
        body["name"] = json!(name);
    }
    if let Some(context) = context {
        body["context"] = json!(context);
    }
    if let Some(locale) = locale {
        body["locale"] = json!(locale.as_str());
    }
    if let Some(token) = turnstile_token {
        body["turnstile_token"] = json!(token);
    }

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/access/request",
        None,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}
