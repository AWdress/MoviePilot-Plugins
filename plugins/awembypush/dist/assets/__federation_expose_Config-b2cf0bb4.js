import { importShared } from './__federation_fn_import-054b33c3.js';

const {resolveComponent:_resolveComponent,createVNode:_createVNode,createElementVNode:_createElementVNode,createTextVNode:_createTextVNode,withCtx:_withCtx,toDisplayString:_toDisplayString,openBlock:_openBlock,createElementBlock:_createElementBlock} = await importShared('vue');


const _hoisted_1 = { class: "awembypush-config pa-4" };
const _hoisted_2 = { class: "d-flex justify-space-between align-center mb-6" };
const _hoisted_3 = { class: "d-flex align-center" };

const {reactive,ref,onMounted} = await importShared('vue');



const _sfc_main = {
  __name: 'Config',
  props: {
  initialConfig: { type: Object, default: () => ({}) },
  api: { type: Object, default: () => ({}) },
},
  emits: ['close', 'switch'],
  setup(__props, { emit: __emit }) {

const props = __props;
const emit = __emit;

const config = reactive({
  enabled: false,
  enable_tmdb: true,
  ...props.initialConfig,
});

const saving = ref(false);
const message = reactive({ show: false, type: 'info', text: '' });

function setMessage(type, text) {
  message.type = type;
  message.text = text;
  message.show = true;
}

async function request(path, options = {}) {
  const apiPath = `plugin/AWEmbyPush${path}`;
  if (options.method === 'POST' && props.api?.post) {
    return props.api.post(apiPath, options.body ? JSON.parse(options.body) : {}, options)
  } else if (props.api?.get) {
    return props.api.get(apiPath, options)
  }
  const response = await fetch(`/api/v1/${apiPath}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  return response.json()
}

async function saveConfig() {
  saving.value = true;
  try {
    const res = await request('/config', {
      method: 'POST',
      body: JSON.stringify(config)
    });
    if (res && res.code === 0) {
      setMessage('success', '配置保存成功');
    } else {
      setMessage('error', res?.message || '配置保存失败');
    }
  } catch (error) {
    setMessage('error', `保存出错: ${error.message}`);
  } finally {
    saving.value = false;
  }
}

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_card_title = _resolveComponent("v-card-title");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_switch = _resolveComponent("v-switch");
  const _component_v_col = _resolveComponent("v-col");
  const _component_v_row = _resolveComponent("v-row");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_snackbar = _resolveComponent("v-snackbar");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createElementVNode("div", _hoisted_2, [
      _createElementVNode("div", _hoisted_3, [
        _createVNode(_component_v_icon, {
          icon: "mdi-cog-outline",
          size: "24",
          class: "mr-2",
          color: "primary"
        }),
        _cache[5] || (_cache[5] = _createElementVNode("div", null, [
          _createElementVNode("h2", { class: "text-h6 font-weight-bold" }, "AWEmbyPush · 配置"),
          _createElementVNode("div", { class: "text-caption text-medium-emphasis" }, "设置 Emby Webhook 通知推送参数")
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
              icon: "mdi-view-dashboard",
              size: "18",
              class: "mr-1"
            }),
            _cache[6] || (_cache[6] = _createTextVNode(" 状态页 ", -1))
          ]),
          _: 1
        }),
        _createVNode(_component_v_btn, {
          color: "primary",
          onClick: saveConfig,
          size: "small",
          loading: saving.value
        }, {
          default: _withCtx(() => [
            _createVNode(_component_v_icon, {
              icon: "mdi-content-save",
              size: "18",
              class: "mr-1"
            }),
            _cache[7] || (_cache[7] = _createTextVNode(" 保存 ", -1))
          ]),
          _: 1
        }, 8, ["loading"]),
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
      class: "mb-4"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card_title, { class: "d-flex align-center text-subtitle-1" }, {
          default: _withCtx(() => [
            _createVNode(_component_v_icon, {
              icon: "mdi-tune",
              size: "20",
              color: "primary",
              class: "mr-2"
            }),
            _cache[8] || (_cache[8] = _createTextVNode(" 基础设置 ", -1))
          ]),
          _: 1
        }),
        _createVNode(_component_v_divider),
        _createVNode(_component_v_card_text, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_row, null, {
              default: _withCtx(() => [
                _createVNode(_component_v_col, {
                  cols: "12",
                  md: "6"
                }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_switch, {
                      modelValue: config.enabled,
                      "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.enabled) = $event)),
                      label: "启用插件",
                      color: "primary",
                      "hide-details": "",
                      density: "compact"
                    }, null, 8, ["modelValue"]),
                    _cache[9] || (_cache[9] = _createElementVNode("div", { class: "text-caption text-medium-emphasis ml-12" }, "开启后才处理 Webhook 通知", -1))
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_col, {
                  cols: "12",
                  md: "6"
                }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_switch, {
                      modelValue: config.enable_tmdb,
                      "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.enable_tmdb) = $event)),
                      label: "开启 TMDB 元数据增强",
                      color: "primary",
                      "hide-details": "",
                      density: "compact"
                    }, null, 8, ["modelValue"]),
                    _cache[10] || (_cache[10] = _createElementVNode("div", { class: "text-caption text-medium-emphasis ml-12" }, "通过 TMDB API 补充中文名、简介、评分等", -1))
                  ]),
                  _: 1
                })
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }),
    _createVNode(_component_v_snackbar, {
      modelValue: message.show,
      "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((message.show) = $event)),
      color: message.type,
      timeout: 3000,
      location: "top"
    }, {
      default: _withCtx(() => [
        _createTextVNode(_toDisplayString(message.text), 1)
      ]),
      _: 1
    }, 8, ["modelValue", "color"])
  ]))
}
}

};

export { _sfc_main as default };
