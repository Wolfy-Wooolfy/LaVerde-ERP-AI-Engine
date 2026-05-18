(function () {
  'use strict';

  var B = 1e9;
  var M = 1e6;

  function formatEGP(value, lang) {
    if (value === null || value === undefined) return '—';
    var s = window.COLLECTIONS_STRINGS || {};
    var egp = s.egp || 'EGP';
    var absVal = Math.abs(value);

    if (absVal >= B) {
      var billions = (value / B).toFixed(2);
      return lang === 'ar'
        ? billions + ' ' + (s.billion || 'مليار') + ' ' + egp
        : billions + 'B ' + egp;
    }
    if (absVal >= M) {
      var millions = (value / M).toFixed(1);
      return lang === 'ar'
        ? millions + ' ' + (s.million || 'مليون') + ' ' + egp
        : millions + 'M ' + egp;
    }
    var rounded = Math.round(value).toLocaleString('en-EG');
    return rounded + ' ' + egp;
  }

  function formatRate(value) {
    if (value === null || value === undefined) return '—';
    return parseFloat(value).toFixed(1) + '%';
  }

  function formatCount(value) {
    if (value === null || value === undefined) return '—';
    return Math.round(value).toLocaleString('en-EG');
  }

  window.CollectionsFormatters = { formatEGP: formatEGP, formatRate: formatRate, formatCount: formatCount };
}());
