import Papa from "papaparse";
import { useMemo } from "react";

/** Render bounded CSV as literal cells; never evaluate formulas or markup. */
export function CsvPreview({
  text,
  truncated,
}: {
  text: string;
  truncated: boolean;
}) {
  const parsed = useMemo(
    () =>
      Papa.parse<string[]>(text, {
        delimiter: ",",
        dynamicTyping: false,
        skipEmptyLines: true,
        preview: 202,
      }),
    [text],
  );
  // The byte limit may split a quoted field or record. Never present it as complete.
  const rows =
    truncated && !parsed.meta.truncated
      ? parsed.data.slice(0, -1)
      : parsed.data;
  const errors = parsed.errors.filter(
    (error) => !(truncated && error.row === parsed.data.length - 1),
  );
  if (errors.length)
    return (
      <>
        <p>CSV could not be displayed as a table. Showing its text.</p>
        <pre className="whitespace-pre-wrap break-words text-xs">{text}</pre>
      </>
    );
  if (!rows.length) return <p>No complete rows to preview.</p>;
  const visible = rows.slice(0, 201);
  const columns = Math.min(50, Math.max(...visible.map((row) => row.length)));
  return (
    <>
      <table
        className="w-full border-collapse text-left text-xs"
        aria-label="CSV preview"
      >
        <thead className="sticky top-0 bg-muted">
          <tr>
            {Array.from({ length: columns }, (_, index) => (
              <th
                key={index}
                scope="col"
                className="border px-2 py-1 font-medium whitespace-pre-wrap"
              >
                {visible[0][index] ?? ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.slice(1).map((row, index) => (
            <tr key={index}>
              {Array.from({ length: columns }, (_, column) => (
                <td
                  key={column}
                  className="max-w-80 border px-2 py-1 align-top whitespace-pre-wrap break-words"
                >
                  {row[column] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(rows.length > 201 ||
        visible.some((row) => row.length > 50) ||
        truncated) && (
        <p className="detail-metadata">
          Table preview is limited to 200 data rows, 50 columns and complete
          records in the first 64 KiB. Download the original for all data.
        </p>
      )}
    </>
  );
}
