const COLUMN_WIDTH_KEY = "skusColumnWidths";

function getStoredWidths() {
  try {
    return JSON.parse(localStorage.getItem(COLUMN_WIDTH_KEY) || "{}");
  } catch {
    return {};
  }
}

function setWidth(table, column, px) {
  const targetTh = table.querySelector(`th[data-column="${column}"]`);
  if (targetTh) {
    targetTh.style.width = `${px}px`;
  }
  table.querySelectorAll(`td[data-column="${column}"]`).forEach((cell) => {
    cell.style.width = `${px}px`;
  });
}

function persistWidths(widths) {
  localStorage.setItem(COLUMN_WIDTH_KEY, JSON.stringify(widths));
}

function initResizableTable() {
  const table = document.querySelector('table[data-resizable="skus"]');
  if (!table) {
    return;
  }

  const widths = getStoredWidths();

  const columns = Array.from(table.querySelectorAll('th[data-column]'));

  columns.forEach((th) => {
    const column = th.dataset.column;
    const width = widths[column];
    if (width) {
      setWidth(table, column, width);
    }
  });

  const handles = Array.from(table.querySelectorAll(".resize-handle"));

  handles.forEach((handle) => {
    const column = handle.dataset.column;
    const th = handle.closest("th");
    let dragging = null;

    const startDrag = (event) => {
      event.preventDefault();
      dragging = {
        column,
        startX: event.clientX,
        startWidth: th.getBoundingClientRect().width,
      };
      document.addEventListener("mousemove", onDrag);
      document.addEventListener("mouseup", endDrag);
    };

    const onDrag = (event) => {
      if (!dragging) return;
      const delta = event.clientX - dragging.startX;
      const newWidth = Math.max(60, dragging.startWidth + delta);
      setWidth(table, dragging.column, newWidth);
    };

    const endDrag = () => {
      if (!dragging) return;
      const finalWidth = th.getBoundingClientRect().width;
      widths[dragging.column] = Math.round(finalWidth);
      persistWidths(widths);
      dragging = null;
      document.removeEventListener("mousemove", onDrag);
      document.removeEventListener("mouseup", endDrag);
    };

    handle.addEventListener("mousedown", startDrag);

    handle.addEventListener("touchstart", (event) => {
      startDrag(event.touches[0]);
      const moveHandler = (touchMove) => onDrag(touchMove.touches[0]);
      const endHandler = () => {
        endDrag();
        document.removeEventListener("touchmove", moveHandler);
        document.removeEventListener("touchend", endHandler);
      };
      document.addEventListener("touchmove", moveHandler);
      document.addEventListener("touchend", endHandler);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initResizableTable);
} else {
  initResizableTable();
}
