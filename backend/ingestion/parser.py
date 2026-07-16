import pandas as pd


REQUIRED_COLUMNS = [
    "Job Type",
    "Business Unit",
    "Opportunity",
    "Sales from Leads Created",
    "Created Date",
    "Cancelled Date",
    "Assigned Technicians",
    "Jobs Estimate Sales Subtotal",
    "Tags",
    "Cancel Reason",
    "Completion Date",
]


def load_spreadsheet(file_path: str) -> pd.DataFrame:
    df = pd.read_excel(file_path)
    df.columns = [col.strip() for col in df.columns]
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df["Created Date"] = pd.to_datetime(df["Created Date"])
    return df
