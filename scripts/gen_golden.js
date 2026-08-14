// Generate evals/datasets/customer360/outstanding.jsonl.
//
// This is a deliberately INDEPENDENT implementation of the same money rules as
// src/ca/customer360.py, written in the shell rather than in Python. The eval
// suite passes only when two separately-written implementations agree, which is
// what makes the golden file trustworthy rather than self-confirming.
//
//   mongosh "$MONGO_URL" --quiet --file scripts/gen_golden.js > evals/datasets/customer360/outstanding.jsonl

const d = db.getSiblingDB("sf_tenant_6a33b5b2091da2fb4a7c3de4");
const AS_OF = new Date("2026-04-23T00:00:00Z"); // newest voucher date in this book

// A spread: high-volume dealer, mid, small, pre-book-heavy, and no-activity.
const NAMES = [
  "Aadinath Traders, Siyaganj",
  "Shree Ji Traders, Bangali Square, Sanwid Nagar",
  "Aakash Traders, Sch No 78, Niranjanpur",
  "Aadarsh Kirana, Narayan Bagh, Rambagh",
  "A B Cosmetic & Foods, RTO",
  "Abdullaganj, Samarth Traders",
];

const posting = { "flags.isCancelled": false, "flags.isDeleted": false, "flags.isOptional": false };
const bucketOf = (age) => (age <= 30 ? "0-30" : age <= 60 ? "31-60" : age <= 90 ? "61-90" : "90+");
const r2 = (x) => Math.round(x * 100) / 100;

NAMES.forEach((name, i) => {
  const led = d.ledgers.findOne({ ledgerName: name });
  if (!led) return;
  const q = Object.assign({ $or: [{ partyLedgerName: name }, { "ledgerEntries.ledgerName": name }] }, posting);

  const owed = (v) => -v.ledgerEntries.filter((e) => e.ledgerName === name).reduce((s, e) => s + (e.amount || 0), 0);

  const invoices = {};
  d.vouchers.find(Object.assign({ voucherCategory: "Sales" }, q)).forEach((v) => {
    const n = v.voucherNumber;
    if (!n) return;
    const prev = invoices[n] || { date: null, amount: 0 };
    invoices[n] = { date: prev.date || v.dates.date, amount: prev.amount + owed(v) };
  });

  const alloc = {};
  let onAccount = 0, advance = 0, newRef = 0, receipted = 0;
  d.vouchers.find(Object.assign({ voucherCategory: "Receipt" }, q)).forEach((v) => {
    receipted += -owed(v);
    v.ledgerEntries.filter((e) => e.ledgerName === name).forEach((e) => {
      (e.billAllocations || []).forEach((b) => {
        const amt = b.amount || 0;
        if (b.billType === "Agst Ref" && b.name) alloc[b.name] = (alloc[b.name] || 0) + amt;
        else if (b.billType === "Advance") advance += amt;
        else if (b.billType === "New Ref") newRef += amt;
        else if (b.billType === "On Account") onAccount += amt;
      });
    });
  });

  const ageing = { "0-30": 0, "31-60": 0, "61-90": 0, "90+": 0 };
  let outstanding = 0, openBills = 0, invoicedTotal = 0, allocatedTotal = 0;
  Object.keys(invoices).forEach((n) => {
    const inv = invoices[n];
    const paid = alloc[n] || 0;
    invoicedTotal += inv.amount;
    allocatedTotal += paid;
    const rem = r2(inv.amount - paid);
    if (rem <= 0.01) return;
    openBills += 1;
    outstanding += rem;
    const age = Math.round((AS_OF - inv.date) / 86400000);
    ageing[bucketOf(age)] = r2(ageing[bucketOf(age)] + rem);
  });

  let preBook = 0;
  Object.keys(alloc).forEach((n) => { if (invoices[n] === undefined) preBook += alloc[n]; });

  print(JSON.stringify({
    case_id: "C360-" + String(i + 1).padStart(3, "0"),
    customer_id: String(led._id),
    input: "What does " + name + " owe as of 23-Apr-2026?",
    context: { ledger_name: name, as_of: "2026-04-23" },
    expected: {
      outstanding: r2(outstanding),
      open_bill_count: openBills,
      invoiced_total: r2(invoicedTotal),
      receipted_total: r2(receipted),
      allocated_total: r2(allocatedTotal),
      pre_book_settlements: r2(preBook),
      on_account: r2(onAccount),
      advance: r2(advance + newRef),
      ageing: ageing,
    },
    tags: ["customer360", "outstanding", "golden"],
  }));
});
