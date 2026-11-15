const colors = ["#2563a8", "#087f5b", "#7c3aed", "#9a5700", "#0e7490", "#a21caf", "#526b15", "#44546a"];
const symbols = ["circle", "square", "diamond", "triangle-up", "triangle-down", "cross", "x", "star"];

export function sectionStyle(index, values, threshold) {
  const color = colors[index % colors.length];
  const critical = values.map((value) => Number.isFinite(value) && threshold !== null && value <= threshold);
  return {
    line: {color},
    marker: {
      symbol: symbols[(index + Math.floor(index / colors.length)) % symbols.length],
      color: critical.map((low) => low ? "#dc2626" : color),
      size: critical.map((low) => low ? 11 : 5),
      line: {color: critical.map((low) => low ? "#7f1d1d" : color), width: 1},
    },
  };
}
