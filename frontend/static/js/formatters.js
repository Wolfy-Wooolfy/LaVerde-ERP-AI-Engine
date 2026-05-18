(function () {
  'use strict';

  var B = 1e9;
  var M = 1e6;

  // opts.fullValue = true → full grouped number with 2 decimal places
  function formatEGP(value, lang, opts) {
    if (value === null || value === undefined) return '—';
    var s = window.COLLECTIONS_STRINGS || {};
    var egp = s.egp || 'EGP';
    var options = opts || {};

    if (options.fullValue) {
      var locale = lang === 'ar' ? 'ar-EG' : 'en-EG';
      return value.toLocaleString(locale, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }) + ' ' + egp;
    }

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
    var locale2 = lang === 'ar' ? 'ar-EG' : 'en-EG';
    return Math.round(value).toLocaleString(locale2) + ' ' + egp;
  }

  function formatRate(value, lang) {
    if (value === null || value === undefined) return '—';
    return parseFloat(value).toFixed(1) + '%';
  }

  function formatCount(value, lang) {
    if (value === null || value === undefined) return '—';
    var locale = lang === 'ar' ? 'ar-EG' : 'en-EG';
    return Math.round(value).toLocaleString(locale);
  }

  window.CollectionsFormatters = {
    formatEGP: formatEGP,
    formatRate: formatRate,
    formatCount: formatCount,
  };
}());
