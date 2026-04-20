# TinyURL API

Base URL for local development:

```text
http://127.0.0.1:5000
```

## Health

```http
GET /health
```

Response:

```json
{"status": "ok"}
```

## Register

```http
POST /api/auth/register
Content-Type: application/json
```

```json
{
  "email": "person@example.com",
  "password": "password123"
}
```

Returns an access token:

```json
{
  "access_token": "...",
  "user": {
    "id": 1,
    "email": "person@example.com"
  }
}
```

## Login

```http
POST /api/auth/login
Content-Type: application/json
```

```json
{
  "email": "person@example.com",
  "password": "password123"
}
```

## Shorten URL

```http
POST /api/shorten
Content-Type: application/json
Authorization: Bearer <access_token>
```

`Authorization` is optional. If present, the URL belongs to that user and appears in `/api/urls`.

```json
{
  "long_url": "https://example.com/article?id=1234",
  "custom_alias": "article123",
  "expires_at": "2026-12-31T23:59:59Z"
}
```

Only `long_url` is required. Duplicate public URLs return the existing short URL unless a custom alias is supplied.

## Redirect

```http
GET /<short_key>
```

Returns a `302` redirect to the original URL.

## Get URL Metadata

```http
GET /api/url/<short_key>
```

Returns the mapping, click count, creation time, and expiry.

## List My URLs

```http
GET /api/urls
Authorization: Bearer <access_token>
```

Returns authenticated user-owned URLs.

## Delete URL

```http
DELETE /api/url/<short_key>
Authorization: Bearer <access_token>
```

Deletes a user-owned short URL.
