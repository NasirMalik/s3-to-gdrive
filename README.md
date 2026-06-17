# s3-to-gdrive

A small CLI tool that downloads files from an S3 bucket and uploads them to a Google Drive folder.

## Requirements

- Python 3.8+
- AWS CLI installed and configured
- Python packages:

```bash
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2 pyyaml requests
```

## Setup

### 1. Install the script

```bash
curl -o ~/bin/s3gdrive https://raw.githubusercontent.com/NasirMalik/s3-to-gdrive/main/s3gdrive
chmod +x ~/bin/s3gdrive

# Make sure ~/bin is on your PATH
export PATH="$HOME/bin:$PATH"
```

### 2. Set up Google Drive credentials (one-time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/library/drive.googleapis.com) and enable the Drive API
2. Go to **Credentials → Create Credentials → OAuth client ID**
3. Choose **Desktop app**, download the JSON file
4. Save it as `~/.s3gdrive_credentials.json`

On first run, a browser window opens for authorization. The token is cached at `~/.s3gdrive_token.json` — no re-auth on subsequent runs.

### 3. (Optional) Create a config file

```bash
cp .s3gdrive.example.json ~/.s3gdrive.json
# Edit ~/.s3gdrive.json with your S3 path and Drive folder ID
```

## Usage

```bash
# Run with Confluence update enabled
CONFLUENCE_EMAIL=you@deliveryhero.com \
CONFLUENCE_API_TOKEN=your_token \
./s3gdrive

# Run without Confluence update (omit the env vars)
./s3gdrive
```

The Drive folder ID is the last segment of a folder URL:
`https://drive.google.com/drive/folders/THIS_PART`

## Confluence page update

After uploading all files to Drive, the script automatically:

1. Parses all `default.yml` files downloaded from S3
2. Compares against `~/.s3gdrive_snapshot.json` (created on first run) to detect changes
3. Prints a diff of any added/removed countries or changed config values
4. Rebuilds the **Config Coverage per Country** table at the bottom of the
   [TES PDT Display Format Confluence page](https://deliveryhero.atlassian.net/wiki/spaces/LOGCPL/pages/1730215943)
5. Saves a new snapshot for the next run

The hand-maintained top table (platform summary + notes) is never touched.

### Credentials

Generate an API token at <https://id.atlassian.com/manage/api-tokens> and set:

```bash
export CONFLUENCE_EMAIL=you@deliveryhero.com
export CONFLUENCE_API_TOKEN=your_api_token
```

If the env vars are absent the script skips the Confluence step silently.

## Config file (`~/.s3gdrive.json`)

All keys are optional — CLI flags override them:

```json
{
  "s3_path": "s3://my-bucket/configs/",
  "drive_folder_id": "GOOGLE_DRIVE_FOLDER_ID_HERE",
  "credentials": "~/.s3gdrive_credentials.json"
}
```
