const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3000';

export { API_URL };

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const url = `${API_URL}${path}`;
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });
}

export async function apiAuth(
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<Response> {
  return apiFetch(path, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });
}
