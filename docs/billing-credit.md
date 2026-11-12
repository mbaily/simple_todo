# Invoices, payments and credit

Record a provider invoice before paying it. Invoice charges, cash receipts and allocations are separate records. Charges and allocations use integer cents; cash payments also retain `amount_paid` for existing spending reports.

On the bill's History page:

- **Record invoice** accepts either the charge before credit or the amount owing after credit. Net amounts require the invoice issue date. Automatic calculation also requires known issue dates for the other invoices and no payments, adjustments or other charges on the same issue date, because dates alone cannot establish their order. Otherwise enter **Credit applied shown on invoice**, including zero if none, or enter the charge before credit. This also applies when correcting an invoice. Automatic calculation excludes later events. Available credit is applied automatically in invoice date order; due dates and recording dates provide a fallback for gross invoices with unknown issue dates.
- **Record payment** accepts full or partial cash payments against an invoice.
- **Record extra payment** records actual extra cash paid. Any unused amount remains account credit.
- **Edit invoice** corrects the supplier invoice number, charge, issue/due dates, service period or statement balance. Statement balances are observations of what the supplier showed at issue, separate from current outstanding amounts. Payment edits and deletions recalculate all allocations.

Invoice due-date corrections also update linked cash records. Payment timing always uses the invoice's due date; an unknown due date has no early/late classification. Correcting the latest settled invoice refreshes the next estimated due date, while older invoice edits and explicitly confirmed future dates preserve the current schedule.

Dashboard payment forms stay tied to the outstanding invoice shown when the page loaded. Changing **Actual due date** corrects that invoice while recording the payment. History payment forms and payment edits do not submit the invoice due date, so an older tab cannot undo a later invoice correction.

Submitting the same invoice form again records its charge once, including when credit has already been applied or the invoice has since been paid. A new form with matching invoice dates or details shows a duplicate warning without adding another charge. Review the existing invoice, or choose **Record as a separate invoice** when the provider issued a distinct bill with matching details. Invoice edits and real duplicate cash payments remain separate operations.

Payment and extra-payment forms also record each submission once, including simultaneous requests and retries after an invoice is settled, edited or followed by another invoice. A fresh form records a genuine second cash payment with the same amount and date. Reusing a submitted form with different details is rejected. Payment submission protection survives export/import; existing receipts retain their original IDs and amounts.

Payment corrections also retain their own submission tokens. Retrying an older correction preserves newer dates, amounts and notes without creating another journal entry. Tokens stay tied to the receipt and submitted details, survive export/import and account deletion, and cannot recreate a deleted payment. Invalid corrections are rejected before changing any fields.

Payments and cash refunds represent money already transferred. Their dates must be today or earlier, including when editing records or importing data. A future-dated receipt cannot settle an invoice or advance its reminder. Invoice due dates may still be in the future, and paying today before the invoice due date remains supported.

**Confirm Bill** defaults to recording a new invoice, even when an older invoice shares its due date. Choose **Correct invoice** and a specific invoice to change an existing charge. The selection fills its current details; changing back to a new invoice clears those details. A date-only confirmation cannot move the forecast away from outstanding invoice debt.

Correcting or backfilling an older invoice preserves the fixed estimate for future bills. Confirming the latest invoice updates that estimate to its charge before credit. Date-only confirmations retain their submission tokens: retrying an older form preserves a newer confirmation, even after another invoice has been recorded. Reusing the form with changed details is rejected. This protection survives account deletion and export/import.

Invoice, adjustment and supplier-balance corrections each retain their own submission token. Retrying an earlier correction preserves any newer correction and does not add another journal entry. Tokens are tied to the record and submitted details; a correction token cannot be used to create a new invoice.

Erasing the note in **Edit invoice** clears the saved note. Confirming a correction without supplying a note preserves the existing note. Retrying an earlier note edit preserves later corrections.

Deleted payments, adjustments (including refunds) and supplier-balance records retain their used form tokens. Retrying an old form is rejected; load History again to record a genuinely new entry. Used tokens survive account deletion and export/import, and importing older metadata cannot clear a deletion flag. Original forms recorded before this protection remain recognised through their original financial record and are retained when that record is deleted.

An invoice advances the schedule once, when fully settled. An extra payment against a settled invoice does not advance it again. A historical invoice covered by spare credit uses the latest invoice for forecasting and preserves a separately confirmed future due date. Outstanding invoice debt still controls the next due date. Unknown invoice dates stay blank. Confirmed outstanding amounts override estimates; variable forecasts use recorded invoice charges before credit.

With multiple outstanding invoices, reminders and dashboard payment forms use the earliest known due date, regardless of invoice issue order. Unknown due dates do not hide other overdue invoices; when only undated invoices remain, the account is unscheduled. Cash and credit allocations still follow invoice event order. Editing Next Due Date on a tracked bill recalculates allocations and restores the earliest remaining outstanding due date.

An account's old payment history is preserved as inferred, settled invoices when invoice tracking is first used. These inferred charges are labelled in History and excluded from charge-based forecasts. Existing payment history cannot establish an opening credit automatically. New accounts with a confirmed amount create an invoice immediately; the first legacy Pay action retains compatibility with payment-only history.

Create/Edit Bill includes **Track invoices, payments and account credit**. Enabling it converts existing cash history into inferred settled invoices without changing money paid. Once account history exists, tracking stays enabled. Leave it unchecked for a simple recurring reminder; recording an invoice or adjustment enables tracking automatically.

Supplier invoice numbers are optional. Numbers are unique per bill account after trimming spaces and ignoring letter case. A repeated number is blocked even when choosing a separate invoice. Distinct supplied numbers allow two invoices with otherwise matching details. Without a number, the existing date/details warning remains available.

## Opening balances, adjustments and refunds

In History, **Record adjustment or refund** accepts a positive amount and effective date:

- **Opening credit / Opening debt** represents the unrecorded balance at the start of the history. Only one opening balance is allowed, dated on or before the first recorded event.
- **Supplier credit / Supplier debit** represents a rebate, correction or other account adjustment. These change the balance without appearing as cash payments or cash spending.
- **Cash refund received** records money returned by the supplier. It consumes account credit and reduces actual net spending on its effective date. A new refund cannot exceed the credit recorded on that date; future payments cannot fund an earlier refund. Missing invoice issue dates must be filled before creating or editing a refund; due-date fallbacks cannot establish refundable credit.

All adjustments must take effect today or earlier. The forms, correction routes and JSON imports reject future effective dates before changing balances or reminders. Record an announced supplier credit once it takes effect; future invoice due dates remain supported.

Available credits settle the oldest invoice charges or debit adjustments. Opening debts and supplier debits can be paid using **Record extra payment**. They do not create fictional invoices or advance the recurring invoice schedule. Edits and deletions recalculate balances; if historical funding is removed, an existing refund can leave the account owing. Cash history retains gross receipts, and History shows refunds and net paid separately. Actual bill spending on Expenses deducts refunds and excludes noncash adjustments. Forecast budgeting remains based on invoice charges.

Creating or correcting invoices, payments and adjustments cannot move an account event before its opening balance. Correct the opening date first when backfilling earlier history. Deleting a tracked receipt recalculates invoice debt without restoring the receipt's cash date as a due date; a settled account keeps its existing forecast or confirmed future date.

## Check against supplier statements

History includes **Record supplier balance**, with an as-of date, amount and owing/credit selector. The comparison includes charges, receipts and adjustments through the end of the selected day and excludes later events. Supplier balances recorded on invoices are also compared using their issue date. Recording a supplier balance is an observation, so it does not change the amount paid or owed.

**Supplier minus app** shows the difference in signed cents (owing is positive, credit negative). A difference calls for checking missing events, charge amounts and supplier posting dates. If an invoice issue date is unknown, the comparison is labelled provisional rather than declaring a match; enter missing issue dates to resolve it. Changing financial history recomputes comparisons against the saved supplier observations.

Exports include invoices, receipts, adjustments, supplier balance observations and both allocation collections. Imports rebuild allocations from the financial records so repeated imports do not spend credit twice. Submission protection also survives export/import.

Imports validate all payment amounts before changing any records. If both cash (`amount_paid`) and integer cents (`amount_cents`) are supplied, they must agree; an invalid or conflicting amount stops the entire import and identifies the payment to correct. Older cash-only imports update ledger cents from the cash value, and cents-only records derive the matching cash value. Unknown amounts in legacy payment-only history remain unknown.

Payment creation and corrections also reject amounts that would lose cents in dollar storage or two-decimal display. Receipt rows, paid-cycle summaries and payment journal entries use the bill's currency symbol.

Invoice charges, adjustments and supplier balances are formatted directly from integer cents, including edit forms and invoice correction selections. Saving an unchanged monetary field cannot round it through floating-point formatting. Confirming an invoice or deriving an unknown recurring estimate from a payment also checks precise estimate storage; an unsupported amount rejects the operation without saving partial history.

Imported service-period dates must be valid ISO dates or datetimes, and the final period end cannot precede its start. Omitted dates are preserved; `null` clears a supplied date. Invalid dates do not silently clear existing history.

Ordinary imports validate new refunds and changes to their account, amount or effective date against credit available on that date, using the final history in the upload. Unchanged existing refunds remain recorded even if later funding corrections left the account owing. To restore such historical refunds into an empty database, select **Restore historical refunds from a full backup** in Import Data. This explicit mode requires a version-2 Export Data backup with its export date and complete billing collections; only historical refund credit checks are bypassed. Amount, date, account and opening-balance checks still apply.

Invoice charges, adjustments and supplier balances also require integer cents within SQLite's supported range. Invoice charges cannot be negative; adjustments must be positive. Supplier balances allow negative values for credit and zero for a balanced account. An optional invoice statement balance can be cleared with `null`. Fractional cents, booleans, numeric strings and out-of-range values stop the whole import before any records are changed.

Partial bill imports preserve omitted budgeting settings, including the amount strategy. A name-only update cannot switch a bill from its history-based estimate to a fixed amount. New bills without an amount strategy still default to fixed; supplying a strategy explicitly updates it.

Imported recurrence settings are checked using the final bill settings after all partial updates in the file. The frequency must be supported, custom intervals must be positive integer days that keep the next date within the supported range, and an optional anchor must be an integer from 1 to 31. Omitted settings are preserved; new bills without a frequency default to monthly. Invalid settings stop the entire import before changing records, so they cannot break Bills or Expenses.

Create/Edit Bill also checks that the recurrence can advance within the supported date range. An interval that overflows is rejected without saving the bill or changing its history. Recording a later invoice or payment also rejects a date-range overflow rather than failing with a server error.

Opening-balance imports follow the same account rules as the forms: each account may have one opening credit or debt, dated on or before its first recorded account event. Existing records and the entire upload are checked together, including invoice-only or payment-only date corrections. A valid correction updates the existing opening record; importing it again does not add another balance. Invalid duplicates or dates roll back the whole upload.

Recurring estimates in Create/Edit Bill and JSON imports use the same monetary validation as invoice charges: finite, non-negative amounts with at most two decimal places, stored without losing cents. Zero is valid; a blank or `null` estimate means unknown. Dollar signs and grouping commas are supported. Invalid estimates are rejected before changing any bill fields, creating inferred history or updating reminders.

Imports are atomic. If any record, relationship or submission metadata fails, the entire upload is rolled back, including earlier successful rows and partial changes to the failed record. The error identifies the collection and record when available and says that no changes were saved. Correct the file and import it again; the success message appears only after all records have been saved.

Folder imports validate the complete hierarchy before attaching parents. A folder cannot be its own parent or sit under one of its descendants, and its parent must exist in the database or the upload. Invalid relationships roll back the whole import and identify the folder to correct. A valid parent can appear after its child in the file. Omitted parents preserve existing nesting; an explicit `null` moves the folder to the root.

Collected task links require both a list ID and a task ID, with both records present in the database or the same upload. Missing references stop the import and roll back all changes rather than silently dropping those links. Valid links retain their supplied sort order.

Exported list and task delays and task alarm dates are restored as dates, so exports containing those features can be imported into existing or empty databases. Invalid dates stop the import without saving changes.

Partial imports preserve omitted tags on lists, tasks and calendar events, and omitted inclusion/exclusion selections on named searches and smart folders. Explicit empty lists clear only the supplied relationship; `null` also preserves existing selections. Full exports still replace supplied relationships, including their filter flags, when restored.

Expense amounts in Create/Edit Expense and JSON imports must be finite, greater than zero and have at most two decimal places, without losing cents when stored. Invalid amounts stop the operation before changing expense fields, journal entries or other imported records. Positive amounts, formatted dollar values and partial imports that omit the amount remain supported.

Partial expense imports preserve the original expense date when it is omitted, blank or `null`, keeping spending in its original reporting period. Today's date defaults only for new expenses without a supplied date; an explicit date corrects the existing date.

Partial payment imports preserve the original payment and due dates when those fields are omitted or blank. Defaults apply only to new receipts. Imports also validate payment/refund dates before changing any records. Financial corrections recalculate affected invoice reminders: reducing a receipt can restore the overdue invoice date, and settling the remaining debt advances the forecast once. Explicitly confirmed future dates in a saved export remain intact for settled accounts.

Multiple partial updates for one receipt in the same upload preserve the dates from earlier rows rather than resetting them to the pre-import dates. Invoice due-date imports update linked receipts and refresh the latest settled forecast, while older corrections and explicit future confirmations preserve the current forecast. Bill-only imports cannot move a reminder away from an outstanding invoice's due date.

Imports cannot disable invoice tracking on accounts with recorded history or attach a payment to another account's invoice. They check existing and incoming records together, including invoice-only changes, before saving anything. Invalid references stop the entire import with a message identifying the record to correct. Legacy cash-only reminders and empty accounts still support disabled tracking. Payment edits also reject an invoice belonging to another account, preserving both accounts' dates and balances.

Monthly budgets on Bills and Expenses use recurring charge estimates independently of unpaid debt, credit or partial payments. A $100 quarterly invoice still budgets $33.33 per month after an $80 payment leaves $20 owing. Actual spending includes payments and cash refunds through today, excluding future-dated transactions from older databases from weekly, monthly, yearly and all-time totals. Ordinary future-dated expenses remain listed and enter spending totals on their dates.

Bills and Expenses show separate totals for each currency, identified by currency code and symbol. No exchange rates or conversions are assumed. Include Bills combines only expenses, recurring budgets, cash and refunds in the same currency. Category percentages and spending averages use each currency's own history. The full expense ledger and its live filtered totals and averages also keep currencies separate; that ledger retains future-dated entries in its totals.

**Past 7 days** includes today and the previous six calendar dates, using the same inclusive start and end for expenses, bill payments and cash refunds. For example, on 8 October it covers 2–8 October; earlier transactions still count toward their monthly, yearly and all-time totals.

**Every 2 months** advances estimated dates by two calendar months and divides bill amounts by two for monthly budgeting. Quarterly advances by three months. Dates taken from actual invoices always take precedence over these forecasts.

## Regression examples

Billing regression tests use fictional accounts, dates and amounts. They cover duplicate cash receipts, credit allocation, split payments, missing invoice dates, corrections, forecasts, and export/import without embedding an account owner’s statements or installation-specific maintenance scripts.
