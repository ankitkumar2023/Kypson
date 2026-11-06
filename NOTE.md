# RFQ Parser — NOTE

## Approach
I built the parser completely with rules instead of an AI model, because the task needs only six fields and I wanted it to be fast and easy to understand. First the input is cleaned: lowercased, common typos fixed, commas removed from Indian numbers, and "metric ton" or "M.T." converted to "mt". Then it finds every number, including Indian formats like "40 hazaar", "2.5 lakh", "1 lakh 50 hazaar" and "15K". It checks the nearby words to decide what each number means: GSM, micron, quantity, price, phone number, pincode, fill weight or capacity. Fill weights ("25 kg bag") and capacities ("1000 kg capacity") are never taken as the quantity.

Category comes from keyword rules. For example, "jumbo bag" → **fibc**, AD Star and cement bags → **woven_sack**, BOPP laminated woven bags → **bopp_bag**, but BOPP film → **film**. If a message names two products, I keep the first one, because the output allows only one category. For location I used **geonamescache**, which works offline and has Indian cities, countries and many other place names. I try an exact match first, then a fuzzy match for spelling mistakes. A delivery location gets priority over a source location.

## Score
`python score.py my_tests.jsonl` gives **69/69 on every field (100%) in about 0.4 seconds**. To make sure I wasn't just passing my own tests, I made nine separate "try to break it" sets (416 messages) with new wording, typos, form-style messages, prices next to counts, number words ("half ton"), Devanagari, several places and new towns. When something failed, I fixed the parser, not the expected answers; a label was changed only where my own expected answer broke the spec or an earlier rule. **483 of all 485** messages now pass.

## Three messages it gets wrong
1. `woven sack 30k chahiye Nandgaon Khandeshwar mein` → **Nandgaon**. It finds that city first, and the full name "Nandgaon Khandeshwar" is not in my location list, so the shorter match wins.
2. `pouch 1 l 5000` → reads "1 l" as **1 lakh** instead of 1 litre. Both meanings are possible, and without more context it can't tell which the buyer meant.
3. `bopp bags 40k Nagpur and 20k Wardha` → **40000** and **Nagpur**. The output has room for only one quantity and one location; adding both quantities would also be reasonable, but the schema doesn't say.

Other decisions: ranges like 5000-10000 take the lower value, decimal GSM/micron values are rounded to integers, and gauge is converted to microns.

## What I would do with 1,000 real labelled examples
First I'd measure accuracy for each field and make a confusion matrix for category, to see exactly where it fails. I'd keep the number parser because it works well and is easy to explain. For choosing category and quantity, I'd train a small model that runs on CPU, like a CRF or a lightweight multilingual transformer, to tag tokens as quantity, GSM, micron, fill weight or location. Real messages would also give me the actual spellings, abbreviations and place names buyers use.

## How I would know it is working with real buyers
- Store each parser output next to the values the operations team confirms or corrects. Those corrections are free labels, so I can measure accuracy every week.
- Track how often each field is missing, especially category and quantity. A sudden jump probably means buyers are using new wording.
- Every week, manually review a small random sample plus all low-confidence cases, like fuzzy location matches or messages with several quantities.
- Watch business results: fewer manual corrections, and fewer RFQs reaching suppliers with wrong details.

## Tools
I used **Python 3.10+** and **geonamescache==2.0.0** for offline city and country data. I used **Claude Code** to help write the parser, the test cases and the red-team examples. My work was deciding the overall approach, checking the outputs against the assignment rules, testing edge cases myself, finding bugs, and making sure the final output follows the required format.
