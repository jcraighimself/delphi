// Draws one line chart per metric from the JSON the server put in the page.

const data = JSON.parse(document.getElementById('chart-data').textContent)

const css = getComputedStyle(document.documentElement)
const color = (name) => css.getPropertyValue(name).trim()
const theme = {
  accent: color('--accent'),
  text: color('--text'),
  textMuted: color('--text-muted'),
  border: color('--border'),
  bgElevated: color('--bg-elevated'),
  red: color('--red'),
}

Chart.defaults.font.family = "'DM Sans', sans-serif"
Chart.defaults.font.size = 11
Chart.defaults.color = theme.textMuted

// Tooltip times in Eastern, e.g. "Sat, Oct 24, 3:42 PM EDT"
const tooltipTime = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
})

// The server chose the tick positions (on round Eastern times) and their labels.
const tickLabels = new Map(data.ticks.map((tick) => [tick.value, tick.label]))

for (const metric of data.metrics) {
  const canvas = document.getElementById(`chart-${metric.field}`)
  const points = data.times.map((time, i) => ({ x: time, y: metric.values[i] }))

  const datasets = [
    {
      data: points,
      borderColor: theme.accent,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBackgroundColor: theme.accent,
      tension: 0.3,
      spanGaps: false, // leave a gap where the Pi was offline
    },
  ]
  if (metric.field === 'co2_ppm') {
    // Dashed red line at the "open a window" level.
    datasets.push({
      data: [
        { x: data.start, y: data.co2Poor },
        { x: data.end, y: data.co2Poor },
      ],
      borderColor: theme.red,
      borderWidth: 1,
      borderDash: [4, 4],
      pointRadius: 0,
      pointHoverRadius: 0,
    })
  }

  new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'linear',
          min: data.start,
          max: data.end,
          afterBuildTicks: (axis) => {
            axis.ticks = data.ticks.map((tick) => ({ value: tick.value }))
          },
          ticks: { callback: (value) => tickLabels.get(value) ?? '' },
          grid: { display: false },
          border: { color: theme.border },
        },
        y: {
          grace: '10%',
          ticks: { maxTicksLimit: 5 },
          grid: { color: theme.border },
          border: { display: false, dash: [3, 3] },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (item) => item.datasetIndex === 0,
          backgroundColor: theme.bgElevated,
          borderColor: theme.border,
          borderWidth: 1,
          cornerRadius: 8,
          padding: 10,
          titleColor: theme.textMuted,
          titleFont: { weight: 'normal' },
          bodyColor: theme.text,
          bodyFont: { size: 13, weight: '600' },
          displayColors: false,
          callbacks: {
            title: (items) => tooltipTime.format(items[0].parsed.x),
            label: (item) => `${item.parsed.y.toFixed(metric.decimals)} ${metric.unit}`,
          },
        },
      },
    },
  })
}
