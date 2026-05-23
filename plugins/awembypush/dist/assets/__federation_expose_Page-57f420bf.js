import { importShared } from './__federation_fn_import-054b33c3.js';

const {resolveComponent:_resolveComponent,createVNode:_createVNode,createElementVNode:_createElementVNode,createTextVNode:_createTextVNode,withCtx:_withCtx,openBlock:_openBlock,createElementBlock:_createElementBlock} = await importShared('vue');


const _hoisted_1 = { class: "awembypush-page pa-4" };
const _hoisted_2 = { class: "d-flex justify-space-between align-center mb-6" };
const _hoisted_3 = { class: "d-flex align-center" };


const _sfc_main = {
  __name: 'Page',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
},
  emits: ['close', 'switch'],
  setup(__props, { emit: __emit }) {
const emit = __emit;

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_card = _resolveComponent("v-card");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", _hoisted_2, [
      _createElementVNode("div", _hoisted_3, [
        _createVNode(_component_v_icon, {
          icon: "mdi-bell-ring-outline",
          size: "24",
          class: "mr-2",
          color: "primary"
        }),
        _cache[2] || (_cache[2] = _createElementVNode("div", null, [
          _createElementVNode("h2", { class: "text-h6 font-weight-bold" }, "AWEmbyPush"),
          _createElementVNode("div", { class: "text-caption text-medium-emphasis" }, "最近推送记录")
        ], -1))
      ]),
      _createElementVNode("div", null, [
        _createVNode(_component_v_btn, {
          color: "primary",
          variant: "tonal",
          size: "small",
          onClick: _cache[0] || (_cache[0] = $event => (emit('switch'))),
          class: "mr-2"
        }, {
          default: _withCtx(() => [
            _createVNode(_component_v_icon, {
              icon: "mdi-cog",
              size: "18",
              class: "mr-1"
            }),
            _cache[3] || (_cache[3] = _createTextVNode(" 配置 ", -1))
          ]),
          _: 1
        }),
        _createVNode(_component_v_btn, {
          variant: "text",
          size: "small",
          onClick: _cache[1] || (_cache[1] = $event => (emit('close')))
        }, {
          default: _withCtx(() => [
            _createVNode(_component_v_icon, {
              icon: "mdi-close",
              size: "18"
            })
          ]),
          _: 1
        })
      ])
    ]),
    _createVNode(_component_v_card, {
      variant: "outlined",
      class: "pa-8 text-center bg-grey-lighten-4"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_icon, {
          icon: "mdi-information-outline",
          size: "48",
          color: "grey-lighten-1",
          class: "mb-4"
        }),
        _cache[4] || (_cache[4] = _createElementVNode("div", { class: "text-subtitle-1 text-medium-emphasis" }, "更多功能和日志开发中...", -1))
      ]),
      _: 1
    })
  ]))
}
}

};

export { _sfc_main as default };
