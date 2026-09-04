"""
Diagnostic. Run this on the PDF that is still coming out wrong:

    python diagnose.py "Market Risk General Provisions.pdf"
    python diagnose.py "file.pdf" --find "consolidation entities"

It prints the measured body size, the font/size/indent ladder, and a character
level dump of any line matching --find. Paste the output back and it will show
exactly what the footnote markers look like in that file.
"""
import argparse
import collections

import pdfplumber

import textlayer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--find", default=None, help="substring of a bad line")
    ap.add_argument("--page", type=int, default=None, help="1-based page to dump")
    args = ap.parse_args()

    with pdfplumber.open(args.pdf) as pdf:
        body = textlayer.detect_body_size(pdf)
        print(f"pages          : {len(pdf.pages)}")
        print(f"body font size : {body}")

        sizes = collections.Counter()
        fonts = collections.Counter()
        for page in pdf.pages[:10]:
            for c in page.chars:
                if c["text"].strip():
                    sizes[round(c["size"], 1)] += 1
                    fonts[(c["fontname"], round(c["size"], 1))] += 1
        print(f"sizes present  : {dict(sizes.most_common(8))}")
        print("font/size mix  :")
        for k, v in fonts.most_common(8):
            print(f"   {k}  x{v}")

    print("\n--- lines ---")
    for line in textlayer.document_lines(args.pdf, skip_cover=False):
        if args.page and line["page"] != args.page:
            continue
        if args.find and args.find.lower() not in line["text"].lower():
            continue
        flag = "FORMULA" if textlayer.is_formula(line) else "       "
        print(f"{flag} p{line['page']:>3} sz{line['size']:>5} x{line['x0']:>6} "
              f"spread{line['size_spread']} script={int(line['has_script'])} "
              f"| {line['text'][:88]}")

    if args.find:
        print("\n--- raw chars on matching lines (footnote markers included) ---")
        with pdfplumber.open(args.pdf) as pdf:
            for page in pdf.pages:
                rows = collections.defaultdict(list)
                for c in page.chars:
                    rows[round(c["top"], 0)].append(c)
                for top, group in sorted(rows.items()):
                    text = "".join(c["text"] for c in group)
                    if args.find.lower() in text.lower():
                        print(f"\npage {page.page_number} top={top}")
                        for c in group[-14:]:
                            print(f"   {c['text']!r:6} size={c['size']:.1f} "
                                  f"top={c['top']:.1f} x0={c['x0']:.1f} "
                                  f"font={c['fontname']}")
                        return


if __name__ == "__main__":
    main()
