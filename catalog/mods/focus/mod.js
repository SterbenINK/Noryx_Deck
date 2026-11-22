// «Фокус-таймер» — пример мода с кодом. Всё, что мод может, — через window.DeckAPI (см. README дека, раздел «Моды»).
(function(){
  var api = window.DeckAPI;
  if (!api || api.version < 1) return;
  var st = {end:0, work:true};                       // таймер живёт в странице (у каждого телефона свой)
  function left(){ return Math.max(0, Math.round((st.end - Date.now()) / 1000)); }
  function fmt(s){ return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); }
  // своя панель для компоновки: «Моды» → «Новый вид» → «Компоновка» → «Добавить панель» → «Фокус-таймер»
  api.panel('focus', {
    name:{ru:'Фокус-таймер', uk:'Фокус-таймер'},
    opts:[{k:'work', label:{ru:'Работа, мин', uk:'Робота, хв'}, def:25}, {k:'rest', label:{ru:'Отдых, мин', uk:'Відпочинок, хв'}, def:5}],
    render:function(el, opts, ctx){
      var T = ctx.T;
      el.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;height:100%">' +
        '<div class="fz-t mono-f" style="font-size:44px;font-weight:800">--:--</div>' +
        '<div class="fz-s" style="font-size:12px;color:var(--mu);font-weight:700"></div>' +
        '<button class="btn fz-b" style="height:40px;padding:0 18px"></button></div>';
      var t = el.querySelector('.fz-t'), s = el.querySelector('.fz-s'), b = el.querySelector('.fz-b');
      function tick(){
        if (!el.isConnected) return;                 // панель перерисовали — этот таймер больше не нужен
        var run = st.end > Date.now();
        if (st.end && !run){                          // время вышло — меняем работу / отдых
          st.work = !st.work; st.end = 0;
          api.toast(st.work ? T('Отдых окончен — за работу', 'Відпочинок скінчився — до роботи') : T('Перерыв!', 'Перерва!'));
        }
        t.textContent = run ? fmt(left()) : fmt((st.work ? +opts.work || 25 : +opts.rest || 5) * 60);
        s.textContent = st.work ? T('Работа', 'Робота') : T('Отдых', 'Відпочинок');
        b.textContent = run ? T('Стоп', 'Стоп') : T('Старт', 'Старт');
        setTimeout(tick, 500);
      }
      b.onclick = function(){
        st.end = st.end > Date.now() ? 0 : Date.now() + (st.work ? +opts.work || 25 : +opts.rest || 5) * 60000;
        tick();
      };
      tick();
    }
  });
  // вмешательство: на каждое сообщение ПК о смене режима — короткая подсказка (дек обработает его как обычно)
  var last = null;
  api.on('message', function(m){
    if (m.t === 'state' && m.mode && last && m.mode !== last && st.end > Date.now())
      api.toast(api.T('Фокус идёт: ', 'Фокус триває: ') + fmt(left()));
    if (m.t === 'state' && m.mode) last = m.mode;
  });
})();
