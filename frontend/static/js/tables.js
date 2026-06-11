/**
 * tables.js — DataTables initialization with Tailwind-compatible styling
 */

'use strict';

// ── DataTables custom styling ─────────────────────────────────────────────────
const DT_CLASSES = {
  table:    'w-full text-sm',
  thead:    '',
  tbody:    '',
  th:       'px-4 py-3 text-left text-xs font-semibold text-neutral-500 uppercase tracking-wide bg-neutral-50 dark:bg-neutral-900',
  td:       'px-4 py-3 text-neutral-700 dark:text-neutral-300 border-t border-neutral-100 dark:border-neutral-700',
};

// ── Init all DataTables on the page ──────────────────────────────────────────
window.initDataTables = function() {
  if (typeof $ === 'undefined' || typeof $.fn.DataTable === 'undefined') return;

  $('table[id]').each(function() {
    const $table = $(this);
    if ($table.attr('data-dt-init')) return; // already initialized
    $table.attr('data-dt-init', 'true');

    const pageLength = parseInt($table.data('page-length') || 10);
    const searchable = $table.data('search') !== 'false';

    $table.DataTable({
      pageLength,
      searching: searchable,
      lengthChange: false,
      info: true,
      ordering: true,
      responsive: false,
      // Never let DataTables pin an inline width measured while the table is
      // hidden (Alpine tab) — w-full on the table must govern.
      autoWidth: false,
      language: {
        search: '',
        searchPlaceholder: 'Search…',
        info: 'Showing _START_–_END_ of _TOTAL_',
        infoEmpty: 'No entries found',
        infoFiltered: '(filtered from _MAX_)',
        paginate: {
          first: '«',
          last: '»',
          previous: '‹',
          next: '›',
        },
        emptyTable: 'No data available',
        zeroRecords: 'No matching records',
      },
      drawCallback() {
        // Apply Tailwind classes to DataTables-generated DOM
        const wrapper = $table.closest('.dataTables_wrapper');
        wrapper.find('.dataTables_filter input').addClass('form-input ms-2 w-48 text-sm');
        wrapper.find('.dataTables_paginate .paginate_button')
          .addClass('px-3 py-1 rounded-lg text-sm cursor-pointer mx-0.5 transition-colors');
        wrapper.find('.paginate_button.current')
          .addClass('bg-primary-600 text-white')
          .removeClass('bg-neutral-100');
        wrapper.find('.paginate_button:not(.current)')
          .addClass('text-neutral-600 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-700');
        wrapper.find('.dataTables_info')
          .addClass('text-sm text-neutral-500 dark:text-neutral-400');
      },
    });

    // Opt-in: move this table's search box into a designated slot
    // (data-filter-slot="#slot-id"), e.g. the distribution card header.
    // Search behavior is unchanged — only the element's position moves.
    const filterSlot = $table.data('filter-slot');
    if (filterSlot) {
      const slot = document.querySelector(filterSlot);
      if (slot) {
        $table.closest('.dataTables_wrapper').find('.dataTables_filter').appendTo(slot);
      }
    }
  });
};

// ── Re-init after Alpine tab switch ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Watch tab changes and re-init tables inside newly visible tabs
  document.querySelectorAll('[x-show]').forEach(el => {
    const observer = new MutationObserver(() => {
      if (el.style.display !== 'none') {
        initDataTables();
      }
    });
    observer.observe(el, { attributes: true, attributeFilter: ['style'] });
  });
});
