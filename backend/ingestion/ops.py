import json
import asyncio
import anthropic
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


def _load_spreadsheet(file_path: str) -> pd.DataFrame:
    df = pd.read_excel(file_path)
    df.columns = [col.strip() for col in df.columns]
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df["Created Date"] = pd.to_datetime(df["Created Date"])
    return df


def _filter_by_date_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_dt = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)
    sheet_min = df["Created Date"].min()
    sheet_max = df["Created Date"].max()

    if start_dt > end_dt:
        raise ValueError(f"Start date {start} must be before end date {end}")
    if start_dt < sheet_min or end_dt > sheet_max:
        raise ValueError(
            f"Date range {start} to {end} is outside the spreadsheet's available range "
            f"({sheet_min.date()} to {sheet_max.date()})"
        )

    filtered = df[(df["Created Date"] >= start_dt) & (df["Created Date"] <= end_dt)]
    if filtered.empty:
        raise ValueError(f"No records found between {start} and {end}")
    return filtered


class RoiAnalyzer:

    def __init__(self, df: pd.DataFrame):
        self.raw_df = df
        self.business_unit: str | None = None
        self.high_value_diagnostic_types: list[str] = []
        self.high_value_maintenance_types: list[str] = []
        self.high_value_diagnostic_tags: list[str] = []
        self.high_value_maintenance_tags: list[str] = []
        self.timing_reasons: list[str] = []

    @classmethod
    def from_file(cls, file_path: str) -> "RoiAnalyzer":
        return cls(_load_spreadsheet(file_path))

    # --- Filter state ---

    @property
    def df(self) -> pd.DataFrame:
        if self.business_unit:
            return self.raw_df[self.raw_df["Business Unit"] == self.business_unit]
        return self.raw_df

    def set_business_unit(self, business_unit: str | None):
        if business_unit and business_unit not in self.available_business_units():
            raise ValueError(
                f"Business unit '{business_unit}' not found. "
                f"Available: {self.available_business_units()}"
            )
        self.business_unit = business_unit

    def available_business_units(self) -> list[str]:
        return self.raw_df["Business Unit"].dropna().unique().tolist()

    def available_date_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return self.df["Created Date"].min(), self.df["Created Date"].max()

    def slice(self, start: str, end: str) -> pd.DataFrame:
        return _filter_by_date_range(self.df, start, end)

    # --- LLM classifications (run once on upload) ---

    async def run_classifications(self):
        job_types_result, tags_result, cancellations_result = await asyncio.gather(
            self._classify_high_value_job_types(),
            self._classify_high_value_tags(),
            self._classify_timing_cancellations(),
        )
        self.high_value_diagnostic_types = job_types_result["high_value_diagnostic"]
        self.high_value_maintenance_types = job_types_result["high_value_maintenance"]
        self.high_value_diagnostic_tags = tags_result["high_value_diagnostic"]
        self.high_value_maintenance_tags = tags_result["high_value_maintenance"]
        self.timing_reasons = cancellations_result["timing"]

    async def _classify_high_value_job_types(self) -> dict[str, list[str]]:
        job_types = self.df["Job Type"].dropna().unique().tolist()
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": (
                    "You are analyzing job types for a home services company. "
                    "Given the following list of job types, classify each one as either "
                    "'high_value_diagnostic', 'high_value_maintenance', or 'standard'. "
                    "High value diagnostics are jobs where a technician identifies a significant problem "
                    "that leads to a large repair or replacement sale. "
                    "High value maintenance are recurring service jobs with strong upsell potential. "
                    "Return ONLY a JSON object with three keys: 'high_value_diagnostic', "
                    "'high_value_maintenance', and 'standard', each containing a list of job type strings "
                    "from the input. Do not include any explanation.\n\n"
                    f"Job types: {job_types}"
                ),
            }],
        )
        return json.loads(response.content[0].text)

    async def _classify_high_value_tags(self) -> dict[str, list[str]]:
        all_tags = self.df["Tags"].dropna().str.split(",").explode().str.strip()
        tags = all_tags[all_tags != ""].unique().tolist()
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": (
                    "You are analyzing job tags for a home services company. "
                    "Given the following list of tags, classify each one as either "
                    "'high_value_diagnostic', 'high_value_maintenance', or 'standard'. "
                    "High value diagnostic tags indicate jobs where a significant problem was identified "
                    "leading to a large repair or replacement sale (e.g. '10+ Demand', 'Replacement Op'). "
                    "High value maintenance tags indicate recurring service jobs with strong upsell potential "
                    "(e.g. '10+ Maintenance', 'Service Plan', 'Potential Member'). "
                    "Return ONLY a JSON object with three keys: 'high_value_diagnostic', "
                    "'high_value_maintenance', and 'standard', each containing a list of tag strings "
                    "from the input. Do not include any explanation.\n\n"
                    f"Tags: {tags}"
                ),
            }],
        )
        return json.loads(response.content[0].text)

    async def _classify_timing_cancellations(self) -> dict[str, list[str]]:
        reasons = self.df["Cancel Reason"].dropna().unique().tolist()
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": (
                    "You are analyzing cancellation reasons for a home services company. "
                    "Given the following list of cancellation reasons, classify each one as either "
                    "'timing' or 'other'. "
                    "Timing cancellations are those caused by speed or availability issues such as: "
                    "the technician was late, the customer found someone faster, a competitor arrived first, "
                    "another company already completed the repair, tech availability issues, or a missed/late arrival window. "
                    "Return ONLY a JSON object with two keys: 'timing' and 'other', each containing a list "
                    "of cancellation reason strings from the input. Do not include any explanation.\n\n"
                    f"Cancellation reasons: {reasons}"
                ),
            }],
        )
        return json.loads(response.content[0].text)

    # --- Aggregate metrics ---

    def total_sales(self, start: str, end: str) -> float:
        return self.slice(start, end)["Jobs Estimate Sales Subtotal"].sum()

    def tech_generated_leads(self, start: str, end: str) -> float:
        return self.slice(start, end)["Sales from Leads Created"].sum()

    def completed_jobs(self, start: str, end: str) -> int:
        return len(self.slice(start, end)[self.slice(start, end)["Completion Date"].notnull()])

    def completed_opportunities(self, start: str, end: str) -> int:
        s = self.slice(start, end)
        return len(s[s["Opportunity"].notnull() & s["Completion Date"].notnull()])

    def sales_per_job(self, start: str, end: str) -> float:
        return self.total_sales(start, end) / self.completed_jobs(start, end)

    def sales_per_opportunity(self, start: str, end: str) -> float:
        return self.total_sales(start, end) / self.completed_opportunities(start, end)

    def blended_sales_average_job(self, start: str, end: str) -> float:
        return (self.tech_generated_leads(start, end) + self.total_sales(start, end)) / self.completed_jobs(start, end)

    def blended_sales_average_opportunity(self, start: str, end: str) -> float:
        return (self.tech_generated_leads(start, end) + self.total_sales(start, end)) / self.completed_opportunities(start, end)

    # --- High value job counts ---

    def total_high_value_diagnostic_jobs_by_job_type(self, start: str, end: str) -> int:
        s = self.slice(start, end)
        return len(s[s["Job Type"].isin(self.high_value_diagnostic_types)])

    def total_high_value_maintenance_jobs_by_job_type(self, start: str, end: str) -> int:
        s = self.slice(start, end)
        return len(s[s["Job Type"].isin(self.high_value_maintenance_types)])

    def total_high_value_diagnostic_jobs_by_tag(self, start: str, end: str) -> int:
        s = self.slice(start, end)
        mask = s["Tags"].apply(
            lambda t: any(tag in str(t) for tag in self.high_value_diagnostic_tags) if pd.notna(t) else False
        )
        return len(s[mask])

    def total_high_value_maintenance_jobs_by_tag(self, start: str, end: str) -> int:
        s = self.slice(start, end)
        mask = s["Tags"].apply(
            lambda t: any(tag in str(t) for tag in self.high_value_maintenance_tags) if pd.notna(t) else False
        )
        return len(s[mask])

    # --- Cancellations ---

    def total_cancellations(self, start: str, end: str) -> int:
        return len(self.slice(start, end)[self.slice(start, end)["Cancelled Date"].notnull()])

    def total_timing_cancellations(self, start: str, end: str) -> int:
        s = self.slice(start, end)
        return len(s[s["Cancel Reason"].isin(self.timing_reasons)])

    def timing_cancellation_rate(self, start: str, end: str) -> float:
        total = self.total_cancellations(start, end)
        if total == 0:
            raise ValueError("No cancellations found in the given date range")
        return self.total_timing_cancellations(start, end) / total

    def high_value_cancellation_rate_by_job_type(self, start: str, end: str) -> float:
        s = self.slice(start, end)
        all_types = self.high_value_diagnostic_types + self.high_value_maintenance_types
        hv = s[s["Job Type"].isin(all_types)]
        if len(hv) == 0:
            raise ValueError("No high value jobs found by job type in the given date range")
        return len(hv[hv["Cancelled Date"].notnull()]) / len(hv)

    def high_value_cancellation_rate_by_tag(self, start: str, end: str) -> float:
        s = self.slice(start, end)
        all_tags = self.high_value_diagnostic_tags + self.high_value_maintenance_tags
        mask = s["Tags"].apply(
            lambda t: any(tag in str(t) for tag in all_tags) if pd.notna(t) else False
        )
        hv = s[mask]
        if len(hv) == 0:
            raise ValueError("No high value jobs found by tag in the given date range")
        return len(hv[hv["Cancelled Date"].notnull()]) / len(hv)

    # --- Technician metrics ---

    def technicians(self, start: str, end: str) -> list[str]:
        return self.slice(start, end)["Assigned Technicians"].dropna().unique().tolist()

    def technician_total_jobs(self, start: str, end: str, technician: str) -> int:
        s = self.slice(start, end)
        return len(s[(s["Assigned Technicians"] == technician) & s["Completion Date"].notnull()])

    def technician_blended_sales_average(self, start: str, end: str, technician: str) -> float:
        s = self.slice(start, end)
        tech_df = s[(s["Assigned Technicians"] == technician) & s["Completion Date"].notnull()]
        if tech_df.empty:
            raise ValueError(f"No completed jobs found for technician: {technician}")
        return (tech_df["Sales from Leads Created"].sum() + tech_df["Jobs Estimate Sales Subtotal"].sum()) / len(tech_df)

    def technician_high_value_diagnostic_jobs(self, start: str, end: str, technician: str) -> int:
        s = self.slice(start, end)
        return len(s[
            (s["Assigned Technicians"] == technician) &
            s["Completion Date"].notnull() &
            s["Job Type"].isin(self.high_value_diagnostic_types)
        ])

    def technician_high_value_maintenance_jobs(self, start: str, end: str, technician: str) -> int:
        s = self.slice(start, end)
        return len(s[
            (s["Assigned Technicians"] == technician) &
            s["Completion Date"].notnull() &
            s["Job Type"].isin(self.high_value_maintenance_types)
        ])

    def technician_high_value_diagnostic_jobs_by_tag(self, start: str, end: str, technician: str) -> int:
        s = self.slice(start, end)
        mask = (
            (s["Assigned Technicians"] == technician) &
            s["Completion Date"].notnull() &
            s["Tags"].apply(lambda t: any(tag in str(t) for tag in self.high_value_diagnostic_tags) if pd.notna(t) else False)
        )
        return len(s[mask])

    def technician_high_value_maintenance_jobs_by_tag(self, start: str, end: str, technician: str) -> int:
        s = self.slice(start, end)
        mask = (
            (s["Assigned Technicians"] == technician) &
            s["Completion Date"].notnull() &
            s["Tags"].apply(lambda t: any(tag in str(t) for tag in self.high_value_maintenance_tags) if pd.notna(t) else False)
        )
        return len(s[mask])

    def technician_high_value_rate_by_job_type(self, start: str, end: str, technician: str) -> float:
        s = self.slice(start, end)
        tech_df = s[(s["Assigned Technicians"] == technician) & s["Completion Date"].notnull()]
        total = len(tech_df)
        if total == 0:
            return 0.0
        all_types = self.high_value_diagnostic_types + self.high_value_maintenance_types
        return len(tech_df[tech_df["Job Type"].isin(all_types)]) / total

    def technician_high_value_rate_by_tag(self, start: str, end: str, technician: str) -> float:
        s = self.slice(start, end)
        tech_df = s[(s["Assigned Technicians"] == technician) & s["Completion Date"].notnull()]
        total = len(tech_df)
        if total == 0:
            return 0.0
        all_tags = self.high_value_diagnostic_tags + self.high_value_maintenance_tags
        mask = tech_df["Tags"].apply(
            lambda t: any(tag in str(t) for tag in all_tags) if pd.notna(t) else False
        )
        return len(tech_df[mask]) / total

    # --- Period comparison ---

    def compare_periods(
        self,
        pre_start: str,
        pre_end: str,
        post_start: str,
        post_end: str,
    ) -> list[dict]:
        pre_df = self.slice(pre_start, pre_end)
        post_df = self.slice(post_start, post_end)

        pre_techs = set(pre_df["Assigned Technicians"].dropna().unique())
        post_techs = set(post_df["Assigned Technicians"].dropna().unique())
        stayed = list(pre_techs & post_techs)

        results = []
        for tech in stayed:
            pre_jobs = self.technician_total_jobs(pre_start, pre_end, tech)
            post_jobs = self.technician_total_jobs(post_start, post_end, tech)

            pre_hv_rate_by_type = self.technician_high_value_rate_by_job_type(pre_start, pre_end, tech)
            post_hv_rate_by_type = self.technician_high_value_rate_by_job_type(post_start, post_end, tech)

            pre_hv_rate_by_tag = self.technician_high_value_rate_by_tag(pre_start, pre_end, tech)
            post_hv_rate_by_tag = self.technician_high_value_rate_by_tag(post_start, post_end, tech)

            try:
                pre_bsa = self.technician_blended_sales_average(pre_start, pre_end, tech)
                post_bsa = self.technician_blended_sales_average(post_start, post_end, tech)
            except ValueError:
                continue

            results.append({
                "technician": tech,
                "pre_total_jobs": pre_jobs,
                "post_total_jobs": post_jobs,
                "delta_total_jobs": post_jobs - pre_jobs,
                "pre_high_value_rate_by_job_type": pre_hv_rate_by_type,
                "post_high_value_rate_by_job_type": post_hv_rate_by_type,
                "delta_high_value_rate_by_job_type": post_hv_rate_by_type - pre_hv_rate_by_type,
                "pre_high_value_rate_by_tag": pre_hv_rate_by_tag,
                "post_high_value_rate_by_tag": post_hv_rate_by_tag,
                "delta_high_value_rate_by_tag": post_hv_rate_by_tag - pre_hv_rate_by_tag,
                "pre_blended_sales_avg": pre_bsa,
                "post_blended_sales_avg": post_bsa,
                "delta_blended_sales_avg": post_bsa - pre_bsa,
            })
        return results

    def rank_departed_technicians(
        self,
        pre_start: str,
        pre_end: str,
        post_start: str,
        post_end: str,
    ) -> list[dict]:
        pre_df = self.slice(pre_start, pre_end)
        post_df = self.slice(post_start, post_end)

        pre_techs = set(pre_df["Assigned Technicians"].dropna().unique())
        post_techs = set(post_df["Assigned Technicians"].dropna().unique())
        departed = list(pre_techs - post_techs)

        results = []
        for tech in departed:
            try:
                bsa = self.technician_blended_sales_average(pre_start, pre_end, tech)
            except ValueError:
                continue
            results.append({
                "technician": tech,
                "pre_blended_sales_avg": bsa,
                "pre_total_jobs": self.technician_total_jobs(pre_start, pre_end, tech),
            })

        results.sort(key=lambda x: x["pre_blended_sales_avg"], reverse=True)
        for i, row in enumerate(results):
            row["rank"] = i + 1
        return results
