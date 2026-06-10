#Parses NICE-ER pdfs and extracts clinical evidence 
#to convert into json for downstream use in AMFV 
#currently tested on  ng180 preoperative fasting but should work on other docs
import os
import json
import re
import pdfplumber
from pydantic import BaseModel, Field
from typing import List

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "data"))
PDF_PATH = os.path.join(DATA_DIR, "ng180_preoperative_fasting.pdf")
OUTPUT_JSONL = os.path.join(DATA_DIR, "nice_parsed_evidence.jsonl")


class ExtractedFinding(BaseModel):
    raw_text: str = Field(..., description="The complete text summary statement from NICE")
    sample_size: int = Field(-1, description="Extracted n= sample size, defaults to -1 if missing")
    study_count: int = Field(1, description="Number of studies tracking this metric")
    bias_rating: str = Field("unknown", description="Extracted risk of bias framework value")

def parse_prose_line(line: str) -> ExtractedFinding | None:
    
   # Matches patterns (5 studies, n= 317, high risk of bias)
    pattern = r"\((?P<studies>\d+)\s+stud[y|ies]+,\s+n=\s*(?P<sample_size>\d+),\s*(?P<bias>[^)]+)\)"
    match = re.search(pattern, line)
    
    if match:
        studies = int(match.group("studies"))
        sample = int(match.group("sample_size"))
        bias = match.group("bias").strip()
        return ExtractedFinding(
            raw_text=line.strip(),
            sample_size=sample,
            study_count=studies,
            bias_rating=bias
        )
    return None

def parse_nice_evidence_review(pdf_path: str = PDF_PATH, output_path: str = OUTPUT_JSONL):
    # reads the target NICE PDF and extracts structured clinical items.
    print(f"Beginning pipeline extraction on: {pdf_path}")
    all_findings = []
    
  
    grid_settings = {
        "vertical_strategy": "text",       
        "horizontal_strategy": "lines",    
        "snap_tolerance": 4,               # Merge lines that are within 4 pixels of each other
    }
    
    with pdfplumber.open(pdf_path) as pdf:
        for idx in range(7, 45):
            page = pdf.pages[idx]
            text = page.extract_text() or ""
            
            for line in text.split("\n"):
                if "risk of bias" in line.lower():
                    finding = parse_prose_line(line)
                    if finding:
                        all_findings.append(finding.model_dump())
            
            if idx == 19:
                unified_tables = page.extract_tables(table_settings=grid_settings)
                print(f"\n[Table Refinement] Overrode grid constraints on Page 20. New unified table count: {len(unified_tables)}")

    with open(output_path, "w", encoding="utf-8") as f:
        for entry in all_findings:
            f.write(json.dumps(entry) + "\n")
            
    print(f"\nPipeline Complete! Saved {len(all_findings)} structured clinical evidence rows to: {output_path}")

if __name__ == "__main__":
  
    parse_nice_evidence_review()