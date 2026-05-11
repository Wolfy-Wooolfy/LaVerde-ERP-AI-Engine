/**
 * Alpine.js chat drawer component — CRM AI Assistant (Phase 5).
 * Communicates with /api/v1/chat/* endpoints.
 */
function chatDrawer() {
  return {
    open: false,
    messages: [],
    input: '',
    isWaiting: false,
    sessionId: localStorage.getItem('chatSessionId') || _newUUID(),
    suggestedQuestions: [],
    strings: window.CHAT_STRINGS || {},

    async init() {
      localStorage.setItem('chatSessionId', this.sessionId);
      try {
        const resp = await fetch('/api/v1/chat/suggested-questions');
        if (resp.ok) this.suggestedQuestions = await resp.json();
      } catch (_) {/* silent — fallback to empty list */}
    },

    async send(text) {
      text = (text || '').trim();
      if (!text || this.isWaiting) return;

      this.messages.push({ role: 'user', content: text, timestamp: new Date().toISOString() });
      this.input = '';
      this.isWaiting = true;
      await this.$nextTick();
      this._scrollToBottom();

      try {
        const resp = await fetch('/api/v1/chat/message', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: this.sessionId, message: text }),
        });

        if (resp.status === 402) {
          this._showError(this.strings.budget_exceeded || 'Budget exceeded.');
          return;
        }
        if (resp.status === 429) {
          this._showError(this.strings.rate_limit || 'Too many requests.');
          return;
        }
        if (!resp.ok) {
          this._showError(this.strings.error || 'Something went wrong.');
          return;
        }

        const data = await resp.json();
        this.messages.push({
          role: 'assistant',
          content: data.message.content,
          followups: data.suggested_followups || [],
          timestamp: data.message.timestamp,
          intent: data.message.intent,
        });
      } catch (_) {
        this._showError(this.strings.error || 'Network error.');
      } finally {
        this.isWaiting = false;
        await this.$nextTick();
        this._scrollToBottom();
      }
    },

    newSession() {
      this.sessionId = _newUUID();
      localStorage.setItem('chatSessionId', this.sessionId);
      this.messages = [];
    },

    renderMarkdown(text) {
      if (typeof marked !== 'undefined' && marked.parse) {
        return marked.parse(text || '');
      }
      // Fallback: plain text with line breaks
      return (text || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/\n/g, '<br>');
    },

    autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    },

    _scrollToBottom() {
      const el = this.$refs.messageList;
      if (el) el.scrollTop = el.scrollHeight;
    },

    _showError(msg) {
      this.messages.push({ role: 'system', content: msg, timestamp: new Date().toISOString() });
    },
  };
}

function _newUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  // Fallback for older browsers
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}
