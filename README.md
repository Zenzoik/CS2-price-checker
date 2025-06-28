# CS2 Case Price Tracker

<p align="center">
  <strong>English</strong> •
  <a href="README_UA.md">Українська</a>
</p>

A Python script that automatically tracks CS2 (Counter-Strike 2) case prices from Steam Market and updates them in a Google Sheets spreadsheet.

## Features

- 🔍 **Search and Add Cases**: Find cases by name and add them to your tracking list
- 📊 **Automatic Price Updates**: Updates current market prices every 5 minutes
- 📈 **Profit Calculation**: Calculate potential profits based on buy/sell prices
- 🛡️ **Rate Limiting Protection**: Built-in protection against Steam API rate limits
- 🌍 **Universal Compatibility**: Works with Google Sheets in any language/locale

## Prerequisites

- Python 3.7+
- Google account
- Google Cloud Platform account (free tier is sufficient)

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/cs2-case-tracker.git
   cd cs2-case-tracker
   ```

2. **Install required packages**
   ```bash
   pip install -r requirements.txt
   ```

## Setup Instructions

### Step 1: Copy the Google Sheets Template

1. Open the [Template Spreadsheet]([https://docs.google.com/spreadsheets/d/your-template-id](https://docs.google.com/spreadsheets/d/1eShxZQ34gI8dir-6LISCNX-omjF8A2XQJb9vL1jh_bs/edit?usp=sharing))
2. Click **File → Make a copy**
3. Rename it("Template_CS_2_cases")

### Step 2: Create Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the **Google Sheets API** and **Google Drive API**:
   - Go to **APIs & Services → Library**
   - Search for "Google Sheets API" and enable it
   - Search for "Google Drive API" and enable it

### Step 3: Create Service Account Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → Service Account**
3. Fill in the service account details:
   - **Name**: `cs2-tracker-bot`
   - **Description**: `Service account for CS2 case price tracking`
4. Click **Create and Continue**
5. Skip role assignment (click **Continue**)
6. Click **Done**

### Step 4: Generate and Download JSON Key

1. Find your newly created service account in the list
2. Click on the service account email
3. Go to the **Keys** tab
4. Click **Add Key → Create new key**
5. Select **JSON** format
6. Click **Create**
7. The JSON file will download automatically
8. **Rename the file to `service-account.json`**
9. **Move it to the same folder as your `main.py` script**

### Step 5: Grant Spreadsheet Access

1. Open your copied Google Sheets spreadsheet
2. Click the **Share** button
3. Copy the service account email from the JSON file or Google cloud (format: `name@project-id.iam.gserviceaccount.com`)
4. Paste the email in the share dialog
5. Set permission to **Editor**
6. **Uncheck "Notify people"**
7. Click **Share**

## Usage

### Running the Script

```bash
python main.py
```

### Menu Options

**Option 1: Add new cases to track**
- Enter case names (e.g., "breakout case", "chroma case")
- The script will search Steam Market and show you the exact match
- Confirm each case before adding to your tracking list

**Option 2: Start price tracking**
- Automatically updates current market prices every 5 minutes
- Prices are updated in column C (Now price)
- Use `Ctrl+C` to stop tracking and return to menu

**Option 3: Exit**
- Safely exit the program

### Manual Data Entry

After adding cases and running price tracking, manually fill in:
- **Column B (Buy price)**: Your purchase price per case
- **Column E (Quantity)**: Number of cases you own

The spreadsheet will automatically calculate:
- Total investment
- Current total value
- Profit/loss amounts and percentages

## Spreadsheet Structure

| Column | Description |
|--------|-------------|
| A | Hash name (automatically filled) |
| B | Buy price (manual entry) |
| C | Now price (automatically updated) |
| D | Price change % |
| E | Quantity (manual entry) |
| F | BUY TOTAL |
| G | NOW TOTAL |
| H | Profit/Loss |
| I | Result % |

## Troubleshooting

### Common Issues

**"No module named 'gspread'" error**
```bash
pip install gspread oauth2client requests
```

**"service-account.json not found" error**
- Ensure the JSON file is in the same directory as `main.py`
- Check the filename is exactly `service-account.json`

**"Permission denied" error**
- Verify you shared the spreadsheet with the service account email
- Check that the service account has Editor permissions

**"Rate limited" messages**
- This is normal - the script automatically handles Steam API rate limits
- Wait times will be shown in the console

**"Could not find item_nameid" error**
- The case name might be incorrect
- Try searching with the exact name from Steam Market

## Legal Notice

This tool is for educational and personal use only. Please respect Steam's Terms of Service and rate limits. The authors are not responsible for any account restrictions or other consequences of using this tool.


## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

⭐ **Star this repository if you find it helpful!**
