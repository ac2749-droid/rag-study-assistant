"""Generate the three sample documents in samples/ (all text written for this project, free to share).

    python tools/make_sample_pdfs.py

No extra libraries: it writes plain PDF files by hand (Helvetica text, one block of text per page).
"""
import textwrap
from pathlib import Path

PAGE_WIDTH, PAGE_HEIGHT = 612, 792  # US Letter, in points
LEFT, TOP, BOTTOM = 56, 736, 60
BODY_SIZE, HEADING_SIZE = 11, 17
WRAP = 92  # characters per line at 11 pt

DOCUMENTS = {
    "Acme-AirLink-X200-Router-Manual.pdf": {
        "title": "Acme AirLink X200 Wi-Fi Router: User Guide (sample document)",
        "pages": [
            ("Welcome and quick start", [
                "Thank you for choosing the Acme AirLink X200. This guide explains how to set up your router, what the lights "
                "mean, how to keep your network secure and what to do if something goes wrong.",
                "What is in the box: the AirLink X200 router, a 12 V power adapter, one yellow Ethernet cable, a quick start "
                "card and a wall-mount bracket with two screws.",
                "Quick start. 1. Place the router in an open area, away from microwave ovens and thick walls. "
                "2. Connect the yellow Ethernet cable from your modem to the yellow WAN port on the back of the router. "
                "3. Plug in the power adapter and wait about two minutes while the Power light blinks amber. "
                "4. When the Power light turns solid green, join the Wi-Fi network printed on the label under the router. "
                "5. Open the AirLink app or visit airlink.setup in a web browser and follow the steps to name your network "
                "and choose a password. 6. Restart your devices so they reconnect.",
            ]),
            ("Understanding the lights", [
                "The front of the router has four lights. Power: solid green means the router is ready; blinking amber means "
                "it is starting up; solid red means a fault (see Error codes on page 5).",
                "Internet: solid white means the router can reach the internet; blinking white means it is looking for a "
                "connection; off means the yellow WAN cable is not detected.",
                "Wi-Fi: solid blue means both the 2.4 GHz and 5 GHz networks are on; blinking blue means a device is pairing "
                "with WPS; off means Wi-Fi has been switched off in the app.",
                "Guest: solid green means the guest network is on. Guests can use the internet but cannot see your other "
                "devices or files.",
            ]),
            ("Wi-Fi and security", [
                "Network name and password. The default network name and password are printed on the label under the router. "
                "Change both during setup. A good password has at least 12 characters.",
                "Guest network. In the AirLink app choose Guest network and switch it on. You can set it to turn off "
                "automatically after 24 hours.",
                "WPS pairing. Press and hold the WPS button on the side of the router for 3 seconds. The Wi-Fi light blinks "
                "blue for two minutes while you press the WPS button on the device you want to connect.",
                "Parental controls. In the app choose Family, then add each child's devices to a profile. You can block "
                "categories of websites and set a daily internet schedule.",
                "Band steering. The router combines the 2.4 GHz and 5 GHz networks under one name and moves each device to "
                "the faster band when it can. Turn this off in Advanced settings if an older device will not connect.",
            ]),
            ("Resetting and updating", [
                "Restart. To restart the router, unplug the power adapter for 10 seconds and plug it back in. Your settings "
                "are kept.",
                "Factory reset. To erase all settings and return to the defaults, use a paperclip to press and hold the "
                "Reset button on the back for 10 seconds, until the Power light blinks red. Release the button and wait "
                "three minutes. After a factory reset you must set up the router again.",
                "Firmware updates. The router checks for updates every night and installs them between 3 a.m. and 4 a.m. "
                "To update by hand, open the AirLink app, choose Settings, then Firmware, and tap Update now. Never unplug "
                "the router while it is updating: the Power light blinks white during an update.",
            ]),
            ("Troubleshooting and error codes", [
                "No internet. Check that the yellow cable is firmly connected to the WAN port and to your modem. Restart the "
                "modem first, wait two minutes, then restart the router.",
                "Slow Wi-Fi. Move closer to the router, keep it off the floor, and avoid placing it near metal objects. "
                "Devices that support 5 GHz will be faster at short range.",
                "Error codes are shown in the AirLink app. E-12 means no WAN cable was detected. E-31 means the router's "
                "address conflicts with another device; restart your modem to fix it. E-47 means the router has overheated: "
                "unplug it, move it somewhere cooler with space around it, and wait 15 minutes before plugging it in. "
                "E-52 means the firmware is damaged: do a factory reset and then update the firmware.",
                "If an error comes back after these steps, contact Acme Support with your serial number, which is printed "
                "on the label under the router.",
            ]),
            ("Specifications, warranty and safety", [
                "Specifications. Wi-Fi: dual band 2.4 GHz and 5 GHz. Ports: one WAN port and four 1 Gigabit LAN ports. "
                "Power: 12 V, 1.5 A. Size: 160 mm by 160 mm by 45 mm. Weight: 380 grams.",
                "Warranty. Acme provides a two-year limited warranty from the date of purchase. The warranty covers defects "
                "in materials and workmanship. It does not cover damage from liquids, overheating caused by blocking the "
                "vents, or modification of the router.",
                "Safety. Use only the supplied power adapter. Keep the router away from water and do not cover its vents. "
                "Do not open the case: there are no user-serviceable parts inside. Recycle the router and adapter at an "
                "electronics collection point rather than throwing them in household waste.",
            ]),
        ],
    },
    "Northwind-Employee-Handbook.pdf": {
        "title": "Northwind Traders Employee Handbook (sample document)",
        "pages": [
            ("Welcome to Northwind", [
                "This handbook explains how we work at Northwind Traders. It is a sample document written for demonstrations "
                "and does not describe a real company.",
                "Working hours. Our standard week is 40 hours. Core hours are 10 a.m. to 3 p.m. local time, when everyone is "
                "expected to be available. Outside core hours you may start and finish earlier or later, with your "
                "manager's agreement.",
                "Your manager is your first contact for questions about your role, your schedule and your development. "
                "People Operations can help with benefits, leave and anything you would rather not raise with your manager.",
            ]),
            ("Time off", [
                "Vacation. Full-time employees earn 15 days of paid vacation in their first three years and 20 days after "
                "that. Please request vacation at least two weeks in advance through the HR portal. Your manager will reply "
                "within three working days.",
                "Sick leave. You receive 10 paid sick days each year. A doctor's note is needed if you are away for more than "
                "three days in a row. Please tell your manager as early as you can on the first day you are unwell.",
                "Parental leave. New parents are entitled to 12 weeks of fully paid leave, whether they are a birth parent, "
                "an adoptive parent or a foster parent. Leave can be taken in one block or split into two blocks within the "
                "first year.",
                "Public holidays. The company observes ten public holidays each year. The list is posted in the HR portal "
                "every January.",
            ]),
            ("Remote and hybrid work", [
                "Most roles are hybrid: three days a week in the office and two days from home. Your team agrees which days "
                "are the office days so that everyone overlaps.",
                "Home office support. Northwind pays a stipend of $40 per month toward your home internet. You can also "
                "claim up to $300 once for a desk chair or monitor.",
                "Security when working remotely. Always connect to the company VPN when you use company data away from the "
                "office. Never work on company documents on a public computer, and lock your screen when you step away.",
            ]),
            ("Expenses and travel", [
                "Claiming expenses. Submit expense claims within 30 days of spending the money, with a receipt for anything "
                "over $25. Claims are paid with your next salary.",
                "Meals while travelling are reimbursed up to $60 per day. Alcohol is not reimbursed.",
                "Flights. Book economy class for flights shorter than six hours. Longer flights may be booked in premium "
                "economy with your manager's approval.",
                "Approvals. Any single purchase above $500 needs your manager's approval before you buy it. Purchases above "
                "$5,000 also need approval from Finance.",
            ]),
            ("Information security", [
                "Passwords. Company passwords must have at least 14 characters. Use the company password manager and turn on "
                "multi-factor authentication for every work account.",
                "Phishing. If you receive a suspicious email, do not click anything. Report it to "
                "security@northwind.example within one hour, even if you are not sure.",
                "Lost or stolen devices. Report a lost or stolen laptop or phone immediately, day or night, by calling the "
                "security hotline. Do not wait until the next working day.",
                "Removable media. Do not plug personal USB drives into company computers. Use the approved cloud storage "
                "to share files.",
            ]),
            ("Conduct and benefits", [
                "Code of conduct. We treat each other with respect. Harassment and discrimination are never acceptable. "
                "You can report a concern to your manager, to People Operations or, anonymously, through the ethics "
                "hotline.",
                "Health insurance. Your health insurance starts on the first day of the month after you join. Dental and "
                "vision cover are optional.",
                "Retirement. Northwind matches your 401(k) contributions up to 4 percent of your salary, starting after "
                "your first month.",
                "Learning. Each employee has a learning budget of $1,000 per year for courses, books and conferences.",
            ]),
        ],
    },
    "Intro-to-C-Pointers-Study-Notes.pdf": {
        "title": "Introduction to C Pointers: Study Notes (sample document)",
        "pages": [
            ("What is a pointer?", [
                "A pointer is a variable that stores the memory address of another variable. If x is an int, then &x is the "
                "address of x, and a pointer to an int is declared with int *p.",
                "To follow a pointer to the value it points to, use the dereference operator *. After p = &x, the "
                "expression *p means the same thing as x, so *p = 5 changes x to 5.",
                "A pointer that points to nothing should be set to NULL. Dereferencing a NULL pointer crashes the program, "
                "so check a pointer against NULL before you use it when it might be empty.",
            ]),
            ("Pointers and arrays", [
                "The name of an array, used in an expression, becomes a pointer to its first element. So for an array a, "
                "a[i] is exactly the same as *(a + i).",
                "Pointer arithmetic. Adding 1 to a pointer moves it to the next element, not the next byte: if p points to "
                "an int and an int is 4 bytes, then p + 1 is 4 bytes further on. Subtracting two pointers into the same "
                "array gives the number of elements between them.",
                "Strings in C are arrays of char that end with the character '\\0'. A function that receives a string "
                "receives a char pointer to its first character.",
            ]),
            ("Pointer to pointer", [
                "A pointer to a pointer stores the address of another pointer. It is declared with two stars, as in "
                "int **pp. If p is an int * and pp = &p, then *pp is p and **pp is the int that p points to.",
                "Why use one? Because C passes arguments by value, a function that must change a pointer, for example to "
                "make it point to newly allocated memory, needs the address of that pointer, which is a pointer to a "
                "pointer.",
                "The main function uses it too: in int main(int argc, char **argv), argv is a pointer to the first element "
                "of an array of char pointers, one for each command line argument.",
                "A dynamic two-dimensional array is built the same way: allocate an array of row pointers, then allocate "
                "each row, and keep the whole thing in an int ** variable.",
            ]),
            ("Dynamic memory", [
                "malloc(n) asks for n bytes on the heap and returns a pointer to them, or NULL if it fails. Always check "
                "the result. calloc(count, size) does the same but sets every byte to zero. realloc(p, n) changes the size "
                "of an earlier allocation and may move it.",
                "free(p) gives the memory back. Every malloc should have exactly one matching free. Forgetting to free "
                "causes a memory leak, and freeing the same pointer twice (a double free) corrupts the heap and can crash "
                "the program.",
                "After calling free(p), set p to NULL. Using memory after it has been freed is called a use-after-free "
                "bug, and the pointer is then a dangling pointer.",
            ]),
            ("Common mistakes", [
                "Using an uninitialised pointer. A pointer declared with no value contains garbage, so dereferencing it "
                "is undefined behaviour. Initialise pointers to NULL or to a valid address.",
                "Returning the address of a local variable. The variable disappears when the function returns, so the "
                "pointer dangles. Return allocated memory or pass in a buffer instead.",
                "Off-by-one errors. An array of n elements has valid indexes 0 to n - 1. Writing to index n overruns "
                "the array.",
                "sizeof on a pointer. For an array a, sizeof(a) is the size of the whole array, but for a pointer p, "
                "sizeof(p) is only the size of the pointer itself, usually 8 bytes.",
                "Practice question: what does **pp = 7 do if pp is an int ** that points to a pointer p that points to "
                "an int x? It sets x to 7.",
            ]),
        ],
    },
}


def escape(text: str) -> str:
    """Make text safe inside a PDF string: backslash and parentheses need a backslash in front."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_lines(paragraphs: list[str]) -> list[str]:
    """Wrap each paragraph to the page width, with a blank line between paragraphs."""
    lines: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if index:
            lines.append("")
        lines.extend(textwrap.wrap(paragraph, WRAP))
    return lines


def page_stream(heading: str, lines: list[str], footer: str) -> bytes:
    """The drawing instructions for one page: a bold heading, the wrapped text, and a footer."""
    leading = 15
    parts = [
        f"BT /F2 {HEADING_SIZE} Tf {LEFT} {TOP} Td ({escape(heading)}) Tj ET",
        f"BT /F1 {BODY_SIZE} Tf {LEFT} {TOP - 34} Td {leading} TL",
    ]
    for line in lines:
        parts.append(f"({escape(line)}) Tj T*" if line else "T*")
    parts.append("ET")
    parts.append(f"BT /F1 9 Tf {LEFT} {BOTTOM - 20} Td ({escape(footer)}) Tj ET")
    available = TOP - 34 - BOTTOM
    if len(lines) * leading > available:
        raise ValueError(f"page {heading!r} has too much text ({len(lines)} lines)")
    return "\n".join(parts).encode("latin-1")


def build_pdf(title: str, pages: list[tuple[str, list[str]]]) -> bytes:
    """Assemble a complete PDF: a catalog, a page tree, two fonts, and a page + stream for each page."""
    objects: list[bytes] = []  # object number = index + 1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in below, once we know the page tree's number
    page_tree = add(b"")
    regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    page_numbers = []
    for number, (heading, paragraphs) in enumerate(pages, start=1):
        footer = f"{title}  -  Page {number} of {len(pages)}"
        stream = page_stream(heading, wrap_lines(paragraphs), footer)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_numbers.append(add(
            (f"<< /Type /Page /Parent {page_tree} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
             f"/Contents {content} 0 R /Resources << /Font << /F1 {regular} 0 R /F2 {bold} 0 R >> >> >>").encode()
        ))
    kids = " ".join(f"{n} 0 R" for n in page_numbers)
    objects[page_tree - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_numbers)} >>".encode()
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {page_tree} 0 R >>".encode()
    info = add(f"<< /Title ({escape(title)}) /Producer (tools/make_sample_pdfs.py) >>".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog, info, xref_at)
    return bytes(out)


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "samples"
    target.mkdir(exist_ok=True)
    for filename, document in DOCUMENTS.items():
        pdf = build_pdf(document["title"], document["pages"])
        (target / filename).write_bytes(pdf)
        print(f"wrote samples/{filename}: {len(document['pages'])} pages, {len(pdf):,} bytes")


if __name__ == "__main__":
    main()
