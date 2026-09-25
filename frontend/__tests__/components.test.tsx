import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ResultsTable from "@/components/ResultsTable";
import UploadDropzone, { isAcceptedFile } from "@/components/UploadDropzone";
import { ProgressBar } from "@/components/ui";
import type { Row } from "@/services/api";

function row(partial: Partial<Row>): Row {
  return {
    original_row: 2,
    source_value: "nikkilve",
    country: "Germany",
    existing_destination: null,
    status: "MATCH",
    source_status: "EXISTS",
    target_status: null,
    decision: "MATCH",
    confidence: 96,
    matched_id: "NikkLive_",
    review_candidate: null,
    output_destination: "NikkLive_",
    output_remarks: null,
    write_destination: true,
    write_remarks: false,
    reason: "",
    error_message: null,
    manual_verdict: null,
    attempts: 1,
    ...partial,
  };
}

describe("ResultsTable", () => {
  it("renders rows in the given (original Excel) order with decisions", () => {
    const rows = [
      row({}),
      row({
        original_row: 3,
        source_value: "teufeurs",
        country: "France",
        status: "NO_MATCH",
        decision: "NO_MATCH",
        confidence: 12,
        matched_id: null,
        output_destination: null,
      }),
      row({
        original_row: 4,
        source_value: "abc123",
        status: "REVIEW",
        decision: "REVIEW",
        confidence: 78,
        matched_id: null,
        output_destination: null,
        review_candidate: "abc123_tv",
      }),
    ];
    const onSelect = vi.fn();
    render(<ResultsTable rows={rows} sourcePlatform="kick" onSelect={onSelect} />);
    const bodyRows = screen.getAllByTestId(/row-/);
    expect(bodyRows.map((r) => r.getAttribute("data-testid"))).toEqual(["row-2", "row-3", "row-4"]);
    expect(screen.getByText("NikkLive_")).toBeInTheDocument();
    expect(screen.getByText("96%")).toBeInTheDocument();
    expect(screen.getByText("candidate: abc123_tv")).toBeInTheDocument();
    expect(screen.getByText("NO MATCH")).toBeInTheDocument();
    expect(screen.getByText("REVIEW")).toBeInTheDocument();
    fireEvent.click(bodyRows[2]);
    expect(onSelect).toHaveBeenCalledWith(rows[2]);
  });

  it("shows an empty state", () => {
    render(<ResultsTable rows={[]} sourcePlatform="twitch" onSelect={() => {}} />);
    expect(screen.getByText("No rows to show.")).toBeInTheDocument();
  });
});

describe("UploadDropzone", () => {
  it("accepts only Excel workbooks", () => {
    expect(isAcceptedFile("streamers.xlsx")).toBe(true);
    expect(isAcceptedFile("STREAMERS.XLSM")).toBe(true);
    expect(isAcceptedFile("streamers.csv")).toBe(false);
  });

  it("passes a valid file to the handler and rejects others", () => {
    const onFile = vi.fn();
    render(<UploadDropzone onFile={onFile} />);
    const input = screen.getByLabelText("Excel file") as HTMLInputElement;
    const good = new File(["x"], "list.xlsx");
    fireEvent.change(input, { target: { files: [good] } });
    expect(onFile).toHaveBeenCalledWith(good);
    fireEvent.change(input, { target: { files: [new File(["x"], "list.csv")] } });
    expect(onFile).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Please choose an Excel workbook/)).toBeInTheDocument();
  });
});

describe("ProgressBar", () => {
  it("exposes progress accessibly", () => {
    render(<ProgressBar value={76} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "76");
  });
});
