use crate::util::api_request;

pub async fn run(api_url: &str) -> i32 {
    api_request(
        api_url,
        reqwest::Method::GET,
        "/health",
        None,
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}
