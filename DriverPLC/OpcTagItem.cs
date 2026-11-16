using System;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace NetToCXSim.Services
{
    public enum OpcDataType
    {
        Bool,
        Int16,
        UInt16,
        Int32,
        UInt32,
        Float,
        String
    }

    public class OpcTagItem : INotifyPropertyChanged
    {
        private string _tagName = "Tag1";
        private string _address = "0.00";
        private OpcDataType _dataType = OpcDataType.Bool;
        private string _description = "";
        private object _value = "---";
        private string _quality = "Offline";
        private short _qualityCode = 0x00; // 0xC0 = Good, 0x00 = Bad/Offline
        private DateTime _timestamp = DateTime.Now;
        private bool _isActive = true;

        public int ServerHandle { get; set; }
        public int ClientHandle { get; set; }

        public string TagName
        {
            get => _tagName;
            set { if (_tagName != value) { _tagName = value; OnPropertyChanged(); } }
        }

        public string Address
        {
            get => _address;
            set { if (_address != value) { _address = value; OnPropertyChanged(); } }
        }

        public OpcDataType DataType
        {
            get => _dataType;
            set { if (_dataType != value) { _dataType = value; OnPropertyChanged(); } }
        }

        public string Description
        {
            get => _description;
            set { if (_description != value) { _description = value; OnPropertyChanged(); } }
        }

        public object Value
        {
            get => _value;
            set { if (!Equals(_value, value)) { _value = value; OnPropertyChanged(); } }
        }

        public string Quality
        {
            get => _quality;
            set { if (_quality != value) { _quality = value; OnPropertyChanged(); } }
        }

        public short QualityCode
        {
            get => _qualityCode;
            set { if (_qualityCode != value) { _qualityCode = value; OnPropertyChanged(); } }
        }

        public DateTime Timestamp
        {
            get => _timestamp;
            set { if (_timestamp != value) { _timestamp = value; OnPropertyChanged(); } }
        }

        public bool IsActive
        {
            get => _isActive;
            set { if (_isActive != value) { _isActive = value; OnPropertyChanged(); } }
        }

        public event PropertyChangedEventHandler PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string propName = null)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propName));
        }

        public OpcTagItem Clone()
        {
            return new OpcTagItem
            {
                TagName = this.TagName,
                Address = this.Address,
                DataType = this.DataType,
                Description = this.Description,
                IsActive = this.IsActive,
                Value = this.Value,
                Quality = this.Quality,
                QualityCode = this.QualityCode,
                Timestamp = this.Timestamp
            };
        }
    }
}
