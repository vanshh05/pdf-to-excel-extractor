# Universal Regex rules configuration for varying regulatory rulebooks

CONFIGS = {
    "UNIVERSAL_REG": {
        "regulation_name": "CRR/PRA",
        
        # Matches "3.1 Text" OR "Article 94 Text" OR "Article 273a Text"
        "section_regex": r"^(Article\s+[0-9a-zA-Z]+|\d+\.\d+)\s+(.*)",
        
        # Matches "(1) Text" OR "1. Text"
        "sub_clause_regex": r"^(\(\d+\)|\d+\.)\s*(.*)",
        
        # Matches "(a) Text" OR "(i) Text"
        "sub_letter_regex": r"^\(([a-ziv]+)\)\s*(.*)",
        
        # Matches "01/01/2022"
        "date_regex": r"^(\d{2}/\d{2}/\d{4})"
    }
}