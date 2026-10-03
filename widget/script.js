define(['jquery'], function ($) {
  var CustomWidget = function () {
    var self = this;

    this.callbacks = {
      render: function () {
        self.render_template({
          caption: { class_name: 'agente-mastershield', html: '' },
          body: '',
          render: '<div class="widget-form"><p>El agente contesta los chats desde el diseñador de bots.</p></div>'
        });
        return true;
      },

      init: function () { return true; },
      bind_actions: function () { return true; },
      settings: function () { return true; },
      destroy: function () { return true; },

      // Sin esta llamada el widget se guarda pero nunca pasa de "instalar" a
      // "instalado", y entonces no aparece como paso en el diseñador de bots.
      // Devolver true no alcanza: hay que decirle el estado explicitamente.
      onSave: function () {
        self.set_status('installed');
        return true;
      },

      // Lo que Kommo guarda como paso del bot cuando alguien agrega este
      // widget a un escenario: una llamada a nuestro servicio con lo que dijo
      // la persona y a quien pertenece.
      //
      // El agente no contesta en esta llamada —tarda 20 o 30 segundos y Kommo
      // corta a los 2—: confirma la recepcion y responde despues por la
      // `return_url` que viene en el cuerpo.
      onSalesbotDesignerSave: function (handler_code, params) {
        var url = (params && params.url) || '';
        var paso = {
          question: [],
          require: [],
          handlers: [
            {
              handler: 'widget_request',
              params: {
                url: url,
                data: {
                  mensaje: '{{message_text}}',
                  lead: '{{lead.id}}',
                  contacto: '{{contact.id}}',
                  nombre: '{{contact.name}}'
                }
              }
            }
          ]
        };
        return JSON.stringify([paso]);
      }
    };

    return this;
  };

  return CustomWidget;
});
