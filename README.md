# AniRena → Torznab Proxy

A lightweight Flask proxy that translates [AniRena](https://www.anirena.com)'s RSS feed into the [Torznab](https://torznab.github.io/spec-1.3-draft/torznab/) format, making it compatible with Prowlarr and Sonarr.

## Usage

### Docker

```bash
docker run -d -p 5000:5000 niklas2233/anirena-rss-translation:latest
```

### Docker Compose

```bash
docker compose up -d
```

## Adding to Prowlarr

1. Go to **Indexers → Add Indexer → Custom Torznab**
2. Set the URL to `http://<host>:5000`
3. Leave the API key blank (or enter any value)
4. Test and save

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /?t=caps` | Returns indexer capabilities |
| `GET /?t=search&q=<query>` | Returns filtered Torznab feed |
| `GET /?t=tvsearch&q=<query>` | Same as search |
| `GET /download?url=<url>` | Proxies torrent file downloads |

## Notes

- RSS feed is cached for 5 minutes to avoid hammering AniRena
- AI-upscaled resolution tokens (e.g. `AI2160p`, `AI1080p`) are normalized to standard resolutions (`2160p`, `1080p`) for Sonarr compatibility
- All items are tagged with Torznab category `5070` (Anime)
