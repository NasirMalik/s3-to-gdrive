# s3-to-gdrive

A small CLI tool that downloads files from an S3 bucket and uploads them to a Google Drive folder.

## Requirements

- Python 3.8+
- AWS CLI installed and configured
- Python packages: `google-api-python-client`, `google-auth-oauthlib`

```bash
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
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
# Pass everything as flags
s3gdrive --s3-path s3://my-bucket/configs/ --drive-folder-id YOUR_FOLDER_ID

# Or use defaults from ~/.s3gdrive.json
s3gdrive
```

The Drive folder ID is the last segment of a folder URL:
`https://drive.google.com/drive/folders/THIS_PART`

## Config file (`~/.s3gdrive.json`)

All keys are optional — CLI flags override them:

```json
{
  "s3_path": "s3://my-bucket/configs/",
  "drive_folder_id": "GOOGLE_DRIVE_FOLDER_ID_HERE",
  "credentials": "~/.s3gdrive_credentials.json"
}
```
