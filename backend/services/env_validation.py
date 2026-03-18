"""Validation logic for deployment environment variables."""

import httpx


async def validate_supabase_url(url: str) -> tuple[bool, str]:
    """Check if a Supabase endpoint is reachable."""
    if not url.startswith("https://"):
        return False, "URL must start with https://"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/rest/v1/",
                headers={"apikey": "test"},
            )
            if resp.status_code in (200, 401, 403):
                return True, "Supabase endpoint is reachable"
            return False, f"Unexpected status code: {resp.status_code}"
    except httpx.ConnectError:
        return False, "Could not connect to URL"
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except Exception as e:
        return False, f"Validation failed: {str(e)}"


async def validate_supabase_anon_key(url: str, anon_key: str) -> tuple[bool, str]:
    """Validate a Supabase anon key against the given URL."""
    if not url:
        return False, "VITE_SUPABASE_URL must be set first"
    if not url.startswith("https://"):
        return False, "Supabase URL must start with https://"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/rest/v1/",
                headers={
                    "apikey": anon_key,
                    "Authorization": f"Bearer {anon_key}",
                },
            )
            if resp.status_code == 200:
                return True, "Anon key is valid"
            if resp.status_code == 401:
                return False, "Invalid anon key (401 Unauthorized)"
            return False, f"Unexpected response: {resp.status_code}"
    except httpx.ConnectError:
        return False, "Could not connect to Supabase URL"
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except Exception as e:
        return False, f"Validation failed: {str(e)}"


async def validate_ai_api_key(value: str) -> tuple[bool, str]:
    """Detect AI provider from key prefix and make a lightweight API call."""
    if not value or len(value) < 10:
        return False, "API key is too short"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if value.startswith("sk-ant-"):
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": value,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if resp.status_code == 200:
                    return True, "Anthropic API key is valid"
                return False, f"Anthropic API returned {resp.status_code}"

            elif value.startswith("sk-"):
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {value}"},
                )
                if resp.status_code == 200:
                    return True, "OpenAI API key is valid"
                return False, f"OpenAI API returned {resp.status_code}"

            else:
                return False, "Unrecognized AI API key format. Supported: OpenAI (sk-...), Anthropic (sk-ant-...)"
    except httpx.ConnectError:
        return False, "Could not connect to API provider"
    except httpx.TimeoutException:
        return False, "Connection timed out"
    except Exception as e:
        return False, f"Validation failed: {str(e)}"
