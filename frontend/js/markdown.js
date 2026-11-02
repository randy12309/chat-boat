/* Minimal, safe Markdown renderer for assistant messages.
   All input is HTML-escaped first, so only the tags produced here can reach the page. */
angular.module('planAssistant')
  .filter('markdown', function ($sce) {
    function esc(s) {
      return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function inline(escaped) {
      var codes = [];
      var s = escaped.replace(/`([^`\n]+)`/g, function (_, c) { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
      s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      s = s.replace(/\*\*([^*\n]+?)\*\*|__([^_\n]+?)__/g, function (_, a, b) { return '<strong>' + (a || b) + '</strong>'; });
      s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\*)/g, '$1<em>$2</em>');
      s = s.replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?![_\w])/g, '$1<em>$2</em>');
      return s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return '<code>' + codes[+i] + '</code>'; });
    }

    function render(src) {
      var lines = String(src == null ? '' : src).replace(/\r\n?/g, '\n').split('\n');
      var out = [], para = [], list = null, i = 0, m;

      function flushPara() {
        if (para.length) { out.push('<p>' + para.map(function (l) { return inline(esc(l)); }).join('<br>') + '</p>'); para = []; }
      }
      function flushList() {
        if (list) {
          out.push('<' + list.type + '>' + list.items.map(function (t) { return '<li>' + inline(esc(t)) + '</li>'; }).join('') + '</' + list.type + '>');
          list = null;
        }
      }
      function flush() { flushPara(); flushList(); }
      function addItem(type, text) {
        flushPara();
        if (list && list.type !== type) { flushList(); }
        if (!list) { list = { type: type, items: [] }; }
        list.items.push(text);
      }

      for (; i < lines.length; i++) {
        var line = lines[i];
        if (/^\s*```/.test(line)) {
          flush();
          var code = [];
          for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) { code.push(lines[i]); }
          out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>');
        } else if (/^\s*$/.test(line)) {
          flush();
        } else if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
          flush();
          var level = Math.min(m[1].length + 2, 6);
          out.push('<h' + level + '>' + inline(esc(m[2])) + '</h' + level + '>');
        } else if (/^\s*([-*_])\s*(?:\1\s*){2,}$/.test(line)) {
          flush();
          out.push('<hr>');
        } else if ((m = /^\s*[-*+]\s+(.*)$/.exec(line))) {
          addItem('ul', m[1]);
        } else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
          addItem('ol', m[1]);
        } else if ((m = /^\s*>\s?(.*)$/.exec(line))) {
          flush();
          out.push('<blockquote>' + inline(esc(m[1])) + '</blockquote>');
        } else {
          flushList();
          para.push(line);
        }
      }
      flush();
      return out.join('');
    }

    var filter = function (text) { return $sce.trustAsHtml(render(text)); };
    filter.render = render;
    return filter;
  });
