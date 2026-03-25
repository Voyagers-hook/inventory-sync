# Voyagers Hook — Inventory Sync

Automatic two-way inventory synchronisation between **Squarespace** and **eBay**.

- 🔄 Hourly full sync via GitHub Actions (completely free)
- ⚡ Manual sync within ~5 minutes via dashboard flag
- 💰 Per-platform pricing (different prices on SS vs eBay)
- 📊 Sales trend tracking
- 🔔 Low-stock alerts

## Setup

### 1. Supabase Database
1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run `schema/setup.sql`
3. Go to **Project Settings → API** and note your URL, anon key, and service_role key

### 2. GitHub Secrets
Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Value |
|--------|-------|
| `SQUARESPACE_API_KEY` | Your Squarespace API key |
| `EBAY_APP_ID` | eBay App ID (Client ID) |
| `EBAY_CERT_ID` | eBay Cert ID (Client Secret) |
| `EBAY_REFRESH_TOKEN` | eBay OAuth Refresh Token |
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase service_role key |

### 3. First Run
After adding secrets, go to **Actions → Hourly Inventory Sync → Run workflow** to do the first catalogue sync.

## Architecture

```
GitHub Actions (cron, free) ──► sync scripts ──► Squarespace API
                                              └──► eBay API
                                              └──► Supabase DB
                                                       │
                                              Dashboard App ◄───► You
```
