using System.Text.Json;

namespace System.Web.Script.Serialization {
    public sealed class JavaScriptSerializer {
        static readonly JsonSerializerOptions Options = new JsonSerializerOptions { IncludeFields = true, PropertyNameCaseInsensitive = true };
        public int MaxJsonLength { get; set; }
        public T Deserialize<T>(string input) {
            if (MaxJsonLength > 0 && input.Length > MaxJsonLength) throw new ArgumentException("JSON is longer than MaxJsonLength.");
            return JsonSerializer.Deserialize<T>(input, Options);
        }
    }
}
