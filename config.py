# Regex rules configuration for regulatory rulebooks
# Easily editable for different document formats

CONFIGS = {
    "CRR_CCR": {
        "regulation_name": "CCR",
        "section_regex": r"^(\d+\.\d+)\s+(.*)",        # Matches "3.1 A firm must..."
        "sub_clause_regex": r"^\((\d+)\)\s*(.*)",      # Matches "(1) if it has..."
        "sub_letter_regex": r"^\(([a-z])\)\s*(.*)",    # Matches "(a) if 4.1 applies..."
        "date_regex": r"^(\d{2}/\d{2}/\d{4})",         # Matches "01/01/2027"
    }
}
