<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  gym: 'M6 7v10 M3 9v6 M18 7v10 M21 9v6 M6 12h12',
  health: 'M12 20S3 15 3 8.5a4.5 4.5 0 0 1 9-1 4.5 4.5 0 0 1 9 1C21 15 12 20 12 20z M7 12h3l1-3 2 6 1-3h3',
  sport: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z M5.6 5.6l12.8 12.8 M18.4 5.6 5.6 18.4 M3 12c6-5 12-5 18 0 M12 3c-5 6-5 12 0 18',
  wallet: 'M20 7H5a2 2 0 0 1 0-4h13v4 M3 5v14a2 2 0 0 0 2 2h15V7 M20 11h-5v6h5 M16 14h1',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z M12 7v5l3 2',
  arrow: 'M5 19 19 5 M5 5h14v14',
  leaf: 'M20 4C9 2 2 9 6 16s16 1 14-12z M5 21 16 10',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
}
</script>

<template>
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="paths[name] ?? paths.grid" /></svg>
</template>
